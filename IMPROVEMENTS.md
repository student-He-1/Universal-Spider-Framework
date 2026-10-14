# 万能爬虫 — 改进迭代记录

> 每迭代一步,改进点记录于此。引擎实现位于 `examples/universal_crawler.py`,Web 层位于 `web/`。

---

## 迭代 12:存储与清洗的正确性修复

**目的**:修正两条采集路径里会静默产出错误数据、甚至直接建表失败的遗留问题。

**改动文件**:
- `utils/helpers.py`
- `storage/base.py`、`storage/cleaned/postgres_cleaned.py`、`storage/raw/postgres_raw.py`
- `cleaner/rules/engine.py`
- `core/middleware/middlewares.py`
- `scripts/init_db.sql`、`tests/test_engine.py`

**修复内容**:
1. **中文列名不再撞名**：清洗存储按字段名动态建列时,非 ASCII 名(如「书名」「价格」「评分」)此前会被统一压成 `__`,同一张表出现重复列名导致 `CREATE TABLE` 失败,写入时字段还会互相覆盖。新增 `utils/helpers.sanitize_identifier`:转写保留 ASCII,一旦发生有损替换就追加原始名的短哈希消歧,保证不同名得到不同列、同名结果稳定、长度不超 PG 标识符上限。表名同样处理。
2. **唯一键改为 `(site_name, url_hash)`**：原始/清洗表此前用 `UNIQUE(url_hash)`,同一 URL 出现在不同站点时会被视为重复而互相覆盖。DDL 与 `ON CONFLICT` 同步改为复合键,并在建表后调用 `storage/base.ensure_unique_key` 做幂等迁移——自动删除遗留的单列唯一约束、补上复合唯一约束,老库无需手工改表。`scripts/init_db.sql` 同步。
3. **清洗过滤条件真正生效**：`>=` / `<=` 之前在运算符列表里却缺少执行分支,命中后会继续误匹配到 `>` / `<`,`float("= 5")` 抛异常又被 `except` 吞掉并返回 True,导致本该被 `FILTER` 丢弃的数据全部通过。现按运算符长度降序匹配并补齐 `>=` / `<=` 分支;非数值字段无法比较时保留数据并记一条告警(改为不静默丢弃)。
4. **重试退避真实生效**：重试中间件此前把延迟写进 `meta["download_latency"]`,而该字段只是统计值、不会产生等待,重试实际没有任何退避。现改用 `twisted.internet.task.deferLater` 调度,延迟结束后再返回重发请求;`process_response` 显式返回该延迟 Deferred。
5. **回归用例**：新增 SQL 标识符消歧、清洗条件运算、重试退避三类单测;缺少 loguru 时注入最小替身,保证这些用例离线可跑且不产生日志文件。

**验证**:
- `python -m unittest tests.test_engine` → 46 项通过(原 31 项 + 新增 15 项)。
- 改动文件的 Python AST 语法检查通过。
- 说明:本机未安装 psycopg2 / loguru(Scrapy 路径运行环境在别处),PG 建表迁移与清洗规则的连库运行未在本机执行;SQL 生成与迁移动作用替身游标核对通过。

**已知边界**:唯一键迁移只在建表/建表确认时执行一次;若目标库存在跨站点重复的历史数据,复合唯一约束补齐时不会因旧数据报错(复合键不冲突),但旧约束一旦删除不可回滚。

---

## 迭代 11:单机采集资源生命周期与验收基线

**目的**:让断点与内存资源随任务结束正确回收,并补上可复现的回归测试基线。

**改动文件**:`examples/universal_crawler.py`、`web/app.py`、`utils/secrets.py`(新增)、`tests/test_engine.py`(新增)、`tests/smoke_test.py`

**改进内容**:
1. **凭据脱敏抽离**：把脱敏纯函数从 `web/app.py` 抽到 `utils/secrets.py`,解除测试对 Flask app 的导入依赖(避免导入时初始化真实任务库)。
2. **断点生命周期**：新增 `_clear_checkpoint`,任务成功完成后删除断点文件及其残留临时文件;取消/失败保留断点以便续跑。
3. **并发批量落盘**：新增 `_save_checkpoints` 与 `_atomic_write_checkpoint`,`url_pattern` 并发分支改为聚合后一次读 + 一次原子写,消除按页 O(n²) 的 I/O;`_save_checkpoint` 改为内部自守卫。
4. **内存任务淘汰**：`web/app.py` 增加内存任务上限并淘汰最旧的终态任务(SQLite 仍保留完整历史);任务落盘后释放 `result` 引用。
5. **验收基线**：新增 `tests/test_engine.py`(断点隔离/读写/清理、配置校验、安全文件名、脱敏、并发落盘);`tests/smoke_test.py` 补非零退出码。

**验证**:
- `python -m unittest tests.test_engine` → 31 项通过。
- 实时回归:完成任务自动清理断点、取消保留断点、重试复用已完成页只补抓未完成页。

---

## 迭代 10:单机任务生命周期闭环

**目的**:让个人使用时能够追踪历史任务、取消运行中的任务，并在失败或中断后恢复工作。

**改动文件**:
- `web/app.py`
- `examples/universal_crawler.py`
- `web/templates/index.html`、`web/static/js/app.js`、`web/static/css/style.css`

**新增能力**:
1. **SQLite 任务台账**:在 `data/tasks.sqlite3` 保存任务、日志和分页状态;服务启动时把遗留的 `pending`/`running`/`cancelling` 任务标记为 `interrupted`。
2. **明确任务状态**:支持排队、运行、取消中、完成、部分完成、失败、已取消和已中断;网页端可查看最近 50 个任务、日志、结果和失败页号,也可载入历史配置或下载结果。
3. **取消任务**:通过取消事件停止后续分页、分页等待和请求重试退避;已经发出的网络请求会在返回或超时后结束。
4. **按任务隔离结果与断点**:Web 任务写入各自的 `data/tasks/{task_id}/` 目录,并使用独立 checkpoint scope,避免同名任务覆盖导出文件或误用其他任务的断点。
5. **重试与续跑**:`url_pattern` 任务复用已成功保存的页面(包括空页面)并重抓未完成页;按钮翻页和无限滚动从头重新运行。页面失败会被标记,任务结果据此区分失败和部分完成。
6. **凭据脱敏**:任务配置和持久化日志会脱敏敏感请求头、带凭据的代理及 URL 中的认证信息;配置含脱敏凭据的任务要求补回凭据后再运行。
7. **前端竞态处理**:任务提交后补读一次状态,避免很快完成的任务因 WebSocket 事件早于任务 ID 响应而卡在运行状态。

**验证**:
- Python AST 语法检查通过。
- `node --check web/static/js/app.js` 通过。
- `git diff --check` 通过;未运行测试套件。

**已知边界**:取消是协作式的,不能中断正在执行的单次网络请求;direct 请求和 Playwright 导航需要等各自请求超时后才会完全停止。非 `url_pattern` 的重试会重新运行整个任务。

---

## 迭代 9:引擎与配置的可靠性加固

**目的**:减少并发抓取时的请求突发、断点误复用和无效配置,并修正 Web 配置入口的安全与输出路径问题。

**改动文件**:
- `examples/universal_crawler.py`
- `web/app.py`
- `web/static/js/app.js`

**改进内容**:
1. **按主机共享请求节流**:同进程内的分页线程和任务共享请求间隔,避免并发时每个线程各自等待导致实际请求过密。
2. **robots 检查缓存与浏览器策略**:缓存 robots 解析结果及短时获取失败;Playwright 的文档、XHR、fetch 请求以及详情页也执行 robots 检查。获取 robots 失败仍按原策略放行。
3. **断点隔离和原子写入**:断点文件名按目标 URL、分页、字段提取及渲染配置生成签名,避免不同配置复用同一文件;页面记录通过临时文件替换并加锁写入。
4. **配置输入校验**:校验分页类型、URL、字段、请求头、代理、并发、等待和延时等值;direct 渲染拒绝按钮翻页和无限滚动配置。
5. **输出文件名收敛**:对任务名生成安全文件名,避免路径字符影响导出位置。
6. **Web 配置补全与转义**:自定义请求头传入 direct 请求;探测结果和字段编辑器的动态文本经过转义,降低配置内容进入 HTML 时造成脚本注入的风险。

**验证**:
- `git diff --check` 通过;该轮未运行测试套件。

---

## 迭代 8:JSON 接口直取 + stealth 修复

**目的**:1) 让引擎直接抓取返回 JSON 的 API(如天气/股票等数据类接口),为模型喂数据铺路——不再只认 HTML;2) 修复 stealth 注入 bug。

**改动文件**:`examples/universal_crawler.py`

**新增能力**:
1. **`_extract_items_from_json()`**:direct 模式自动识别 JSON 响应(以 `{`/`[` 开头且可解析),按 `api.fields` 提取;自动定位列表(`item_path` 优先 → 根数组 → 子 dict 内平行数组),支持 **open-meteo 式平行数组**(`time[]/max[]/min[]` → 逐条记录)
2. **stealth 注入修复**:新版 `playwright_stealth` 移除了 `stealth_sync`(旧代码 `from playwright_stealth import stealth_sync` 静默失效),改为 `_apply_stealth()` 多策略:Stealth 类 → 旧 API → 手动 JS 兜底(清 webdriver/chrome/plugins 特征)

**验证**:
- open-meteo 天津接口:14 天历史 + 7 天预报 = 21 条,`日期/最高温/最低温` 逐条结构化,落 Excel + SQLite
- 可视化控制台全流程:填 API 配置 → 开始爬取 → 21 条表格 → 统计条更新

> 说明:中国天气网对数据中心 IP 做 HTTP/TLS 层封锁(非 webdriver 检测),stealth 无法绕过,已改用 open-meteo(免费、无 key、结构化 JSON)作为天气数据源。

---

## 迭代 7:工程结构轻整理(脚本归位)

**目的**:根目录文件偏多,把启动/自检脚本统一归入 `scripts/`,保持根目录清爽。

**改动**:
- `run_web.bat`、`run_spider.bat`、`activate_spider.bat`、`verify_env.py` → 移入 `scripts/`
- bat 内部使用绝对路径(`cd /d D:\桌面\爬虫`),移动后无需改逻辑;仅同步 `activate_spider.bat` 内自检提示为 `python scripts\verify_env.py`
- 同步更新 `README.md` 目录结构与引用路径

**结果**:根目录仅保留 5 个文件(`README.md` / `IMPROVEMENTS.md` / `run.py` / `docker-compose.yml` / `requirements.txt`)

---

## 迭代 6:站点指纹识别(探测器)+ 数据统计面板

**目的**:1) 输入 URL 自动探测目标站的渲染方式/翻页/容器/字段,一键填表,降低写配置门槛;2) 可视化控制台展示 SQLite 数据统计。

**改动文件**:
- `examples/universal_crawler.py`(新增 `probe_site()`)
- `web/app.py`(`/api/probe`、`/api/stats` 路由)
- `web/templates/index.html`、`web/static/js/app.js`、`web/static/css/style.css`(探测面板 + 统计条)

**新增能力**:
1. **站点探测器 `probe_site(url)`**:
   - 探测结果:标题、渲染方式(direct/playwright)、JS动态、WAF挑战、SSR内嵌JSON、翻页类型(next_button/url_pattern/none)
   - 统计常见容器(article/product/item/card/table)频次给候选
   - 识别标题/价格/链接字段候选
2. **`/api/probe`**:Web 界面输入 URL → 探测 → 结果可视化;容器候选点击填 item_selector、字段 chip 点击加字段行、渲染方式自动回填
3. **`/api/stats`**:扫描 `data/storage/*.db`,返回每库的表/行数/字段;前端在状态栏下实时展示

**验证**:
- `probe_site(books.toscrape)` → render=direct, js=False, 容器 `div.product`(20)/`article`(20), 字段 标题/价格
- 语法检查通过

---

## 迭代 5:SQLite 持久化 + 断点续爬

**目的**:1) 数据既能落 Excel/CSV,也能进数据库便于后续分析;2) 任务中断后重跑不重爬已完成页,节省时间与请求。

**改动文件**:
- `examples/universal_crawler.py`
- `web/templates/index.html`、`web/static/js/app.js`(高级区新增断点续爬开关、存储配置)
- `web/app.py`(任务状态透出 sqlite_path)

**新增能力**:
1. **SQLite 存储** `_save_to_sqlite()`:
   - 配置 `storage: {"type":"sqlite","table":"items"}` 启用,与 Excel/CSV 并存
   - 表按结果字段动态创建;已有表缺列自动 `ALTER ADD COLUMN`
   - **幂等写入**:按全字段指纹去重,重复行不重复入库
   - 默认路径 `data/storage/{任务名}.db`;web 任务完成可拿到 sqlite_path
2. **断点续爬** `_load_checkpoint()` / `_save_checkpoint()`:
   - 配置 `resume: true` 启用,按页持久化到 `data/resume/{任务名}.jl`(JSON Lines)
   - 重跑时已完成页从断点直接复用,**不重复下载**;只抓缺失页
   - 并发模式下写 checkpoint 有锁保护

**配置格式**:
```json
{
  "resume": true,
  "storage": { "type": "sqlite", "table": "items" }
}
```

**验证**:
- 单测:SQLite 动态建表/补列/追加 + checkpoint 保存/读取/resume关不读 全过
- 端到端:第一轮爬2页40条 → 第二轮 max_pages=3,日志 `[第 1 页] 已爬过(断点),直接复用 20 条`、`[第 2 页] 已爬过`,只新抓第3页 → 60条;SQLite 幂等:两轮后最终 60 行(第二轮新增仅20)
- `py_compile` 语法检查通过

---

## 迭代 4:反爬加固(代理轮换 + robots 合规 + 后处理)

**目的**:三点一起做——防爬失败(代理轮换/回退直连)、合规(robots + 请求节流)、数据质量(字段后处理)。

**改动文件**:
- `examples/universal_crawler.py`
- `web/templates/index.html`、`web/static/js/app.js`(高级区新增多代理、后处理 JSON)

**新增能力**:
1. **代理轮换池** `_fetch_with_proxy_rotation()`:
   - `proxy` 支持多代理逗号分隔 / `proxies` 列表 / `proxy_file`(每行一个)
   - 请求失败自动切换下一个代理(幂等去重)
   - **全部代理失败自动回退直连**,防爬失败兜底
2. **robots.txt 合规** `_robots_allowed()`:
   - 默认关闭(`respect_robots: true` 开启,避免误伤)
   - 拿不到 robots.txt 一律放行,不阻塞爬取
3. **字段后处理** `_apply_postprocess()`:
   - 逐字段清洗:strip / collapse_ws / lower / upper / digits_only / remove(去子串) / regex_replace
   - 清洗不改原行,返回新字典

**配置格式**:
```json
{
  "proxy": "http://p1:8080,http://p2:8080",
  "proxy_file": "proxies.txt",
  "respect_robots": true,
  "postprocess": { "价格": { "remove": "元", "digits_only": true } }
}
```

**验证**:
- 单测:代理解析(逗号/列表/去重)、代理挑选(排除失败/全失败回退)、后处理(去单位+纯数字+合并空白+大小写+不改原行)、robots 默认关 全过
- 端到端:2 个失效代理 + 并发 + 去重 + 后处理,代理失败回退直连成功抓 40 条,`£51.77 → 51.77`
- `py_compile` 语法检查通过

---

## 迭代 3:数据去重 + 并发抓取

**目的**:提高数据质量(去重防重复)、加速抓取(并发);同时严格防止去重误删数据导致分析偏差。

**改动文件**:
- `examples/universal_crawler.py`
- `web/templates/index.html`、`web/static/js/app.js`(高级区新增去重字段、并发数)

**引擎新增能力**:
1. **数据去重 `_dedupe_results()`**:
   - 默认关闭(不配 `dedup` 就不去重,零风险)
   - 只按明确字段(如「链接」)去重,保序保留第一条
   - **防漏策略**:去重字段为空/缺失的条目永不删除,避免误删导致分析偏差
   - 完成详情回填后统一去重,覆盖跨页/跨并发重复
2. **翻页并发**:direct 的 `url_pattern` 支持 `concurrency`(线程池),按页号排序合并,输出顺序稳定,不随完成顺序乱序
3. **详情页并发**:`detail_concurrency` 配置详情页抓取并发

**配置格式**:
```json
{
  "dedup": { "key": "链接" },   // 去重字段(空/缺失条目不删)
  "concurrency": 4,              // 翻页并发(默认1=串行)
  "detail_concurrency": 8        // 详情页并发(可选)
}
```

**验证**:
- `_dedupe_results` 单测:未配置不去重 / 保序 / 空key永不删 / key不存在全保留 / 全重复留1 全过
- 端到端:模拟 3 页翻页尾部重叠(30 条原始 → 24 唯一),保序正确,空链接 2 条保留
- 并发 vs 串行:quotes.toscrape 2 页均取 20 条,内容完全一致
- `py_compile` 语法检查通过

---

## 迭代 2:API 接口响应捕获(XHR 抓包)

**目的**:让引擎不仅从 DOM 提取,还能直接从网站 JSON 接口拿数据(解决淘宝、微博等大量依赖 XHR 接口的站点)。

**改动文件**:
- `examples/universal_crawler.py`
- `web/templates/index.html`(高级区新增 API 配置输入)
- `web/static/js/app.js`(收集/回填/校验 API 配置)
- `web/static/css/style.css`(textarea 样式)

**引擎新增能力**:
1. **JSON 响应捕获**:playwright 渲染时通过 `page.on("response")` 监听所有 JSON 接口响应并缓存
2. **JSON 点路径提取**:`_json_path()` 支持 `data.items`、`data.items[0].title` 嵌套/索引取值
3. **API 优先提取**:配置了 `api` 时,`_pick_items()` 优先用接口数据,拿不到才回退 DOM
4. **逐页隔离**:url_pattern 下翻页会 `api_responses.clear()`,每页独立抓包,不串页

**配置格式**(Web 界面"高级"区,或 config 直接传 `api`):
```json
{
  "item_path": "data.items",
  "fields": { "书名": "title", "价格": "price" }
}
```
- `item_path`:列表在 JSON 中的点路径(可空 = 整个响应当列表)
- `fields`:列名 → JSON 点路径

**验证**:
- 引擎单测:JSON 路径解析、API 条目提取、边界(嵌套/顶层数组/非法包)全过
- `py_compile` 语法检查通过

---

## 迭代 1:核心缺陷补齐

**目的**:修复引擎的主要短板(代理、stealth、WAF 挑战、无限滚动、重试)。

**改动文件**:
- `examples/universal_crawler.py`(引擎重构)
- `web/templates/index.html`、`web/static/js/app.js`(高级区新增 HTTP 代理输入)

**引擎新增能力**:
1. **代理支持**:`proxy` 配置项,direct(curl_cffi)和 playwright 浏览器都透传;Web 界面可填
2. **stealth 反检测**:playwright 分支自动注入 `playwright_stealth`,绕过 `navigator.webdriver` 检测
3. **WAF/挑战页识别与重放**:direct 请求识别挑战特征(challenge-platform / 验证码 / __jschl 等),命中自动切 playwright 渲染拿真实 HTML;`auto_fallback: false` 可关闭
4. **无限滚动修复**:加 `max_scrolls`(默认 50)兜底防无限加载;按条目指纹去重只取新增;日志显示滚动进度
5. **指数退避重试**:请求失败按 `2^n + jitter` 退避重试,避免集中重试

**Web 界面**:
- 高级区新增「HTTP 代理」输入框(留空不代理)
- 加载模板时回填代理、API 配置

**验证**:
- 引擎单测:挑战识别、HTML 提取、配置归一化全过
- 浏览器全链路:books.toscrape 2 页 40 条爬取落盘 Excel/CSV 成功
