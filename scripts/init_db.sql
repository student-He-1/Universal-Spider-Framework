-- 爬虫框架数据库初始化脚本
-- 由 docker-compose 自动执行

-- 原始数据表（JSONB 存储完整原始数据）
CREATE TABLE IF NOT EXISTS raw_data (
    id SERIAL PRIMARY KEY,
    site_name VARCHAR(128) NOT NULL,
    url TEXT NOT NULL,
    url_hash VARCHAR(64) NOT NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'crawled',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(url_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_site ON raw_data(site_name);
CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_data(status);
CREATE INDEX IF NOT EXISTS idx_raw_created ON raw_data(created_at);

-- 代理池表（可选，也可以全用 Redis）
CREATE TABLE IF NOT EXISTS proxy_pool (
    id SERIAL PRIMARY KEY,
    proxy_url VARCHAR(256) NOT NULL UNIQUE,
    proxy_type VARCHAR(16) DEFAULT 'http',
    fail_count INT DEFAULT 0,
    success_count INT DEFAULT 0,
    avg_speed DOUBLE PRECISION DEFAULT 0,
    anonymity VARCHAR(16) DEFAULT 'unknown',
    last_check TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_proxy_type ON proxy_pool(proxy_type);
CREATE INDEX IF NOT EXISTS idx_proxy_fail ON proxy_pool(fail_count);

-- 清洗任务队列表（用于离线清洗调度）
CREATE TABLE IF NOT EXISTS clean_tasks (
    id SERIAL PRIMARY KEY,
    site_name VARCHAR(128) NOT NULL,
    url_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) DEFAULT 'pending',  -- pending / processing / done / failed
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(site_name, url_hash)
);

CREATE INDEX IF NOT EXISTS idx_clean_status ON clean_tasks(status);
