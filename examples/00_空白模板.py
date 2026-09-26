# -*- coding: utf-8 -*-
"""
================================================================
★★★ 万能框架：爬任何新网站就复制这个文件，照着填空 ★★★
================================================================
用法 3 步：
  1. 复制本文件，改个名字，比如 爬豆瓣电影.py
  2. 把下面 CONFIG 里带 ← 的地方改成你要爬的网站信息
  3. 运行：python 爬豆瓣电影.py
     结果自动存成 Excel 到 data\demo\ 目录

----------------------------------------------------------------
【第一步：判断你的网站是哪种类型，选 render 和 pagination.type】
----------------------------------------------------------------
  情况A：普通静态网站（右键能看到完整内容，翻页网址会变）
         → render = "direct"
         → pagination.type = "url_pattern"

  情况B：JS 动态网站（内容靠 JS 加载，翻页网址不变，要点按钮）
         → render = "playwright"
         → pagination.type = "next_button"

  情况C：无限滚动网站（往下滚自动加载更多，如微博/知乎）
         → render = "playwright"
         → pagination.type = "infinite_scroll"

  情况D：只有一页，不用翻页
         → pagination.type = "none"

----------------------------------------------------------------
【第二步：找"指路牌"(css 选择器)—— 不用背，照做】
----------------------------------------------------------------
  1. 用 Chrome/Edge 打开目标网页
  2. 在你想要的内容上点右键 → 检查(Inspect)
  3. 右边高亮一行代码，在那行代码上再点右键
  4. 点 Copy → Copy selector，就得到指路牌
  5. 小技巧：
       想取文字，指路牌后面加  ::text
       想取链接，指路牌后面加  ::attr(href)
       想取图片，指路牌后面加  ::attr(src)
  6. 一页有很多条时，先找到"包住每一条的盒子"填到 item_selector，
     再在盒子内部找各个字段
================================================================
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from universal_crawler import crawl


# ================================================================
# ↓↓↓ 只需要改这一个 CONFIG ↓↓↓
# ================================================================
CONFIG = {
    # ① 任务名字（会用作 Excel 文件名，随便起，别用特殊符号）
    "name": "我的爬取任务",

    # ② 渲染方式："direct"（普通静态站，快）或 "playwright"（JS动态站，慢）
    "render": "direct",

    # ③ 翻页策略
    "pagination": {
        # 翻页方式："url_pattern" / "next_button" / "infinite_scroll" / "none"
        "type": "url_pattern",

        # --- 如果 type = "url_pattern"，填下面这行（把页码数字换成 {page}）---
        "url_pattern": "https://换成你的网址/page/{page}",
        "start_page": 1,        # 从第几页开始
        "max_pages": 3,         # 爬几页（先填1试通了再加大）

        # --- 如果 type = "next_button"，填下面两行 ---
        # "next_selector": "li.ant-pagination-next",   # 下一页按钮指路牌
        # "disabled_marker": "disabled",                 # 最后一页按钮的标志

        # --- 如果 type = "infinite_scroll"，可填 ---
        # "scroll_pause": 1.5,   # 每次滚动后等几秒
    },

    # ④ 如果是单页/playwright 第一页，填起始网址（url_pattern 模式不用填）
    # "first_url": "https://换成你的网址",

    # ⑤ "包住每一条内容的盒子"的指路牌
    "item_selector": "div.item",          # ← 改成实际的盒子

    # ⑥ 你要的每一列："表格列名": "指路牌"
    #    想要几列就写几行，不想要的删掉
    "fields": {
        "标题": "h3 a::text",
        "链接": "h3 a::attr(href)",
        "价格/日期": "span.price::text",
        # "图片": "img::attr(src)",
    },

    # ⑦ 要不要点进每条的详情页抓更多内容？不需要就整段删掉或设为 None
    # "detail": {
    #     "link_field": "链接",          # 用 fields 里哪一列作为详情页链接
    #     "fields": {
    #         "正文/详情": "div.content::text",
    #         # "作者": "span.author::text",
    #     },
    #     "limit": 0,   # 0=每条都进详情(慢)；先填2试通，再改0
    # },

    # ⑧ 礼貌延时：每抓一页停几秒，别把人家服务器搞挂（建议0.5~2）
    "delay": 1.0,
}
# ================================================================
# ↑↑↑ 只需要改上面这一个 CONFIG ↑↑↑
# ================================================================


if __name__ == "__main__":
    data = crawl(CONFIG)
    print(f"\n完成！共 {len(data)} 条")
    print("去 data\\demo\\ 目录找同名 Excel 文件，双击打开")
