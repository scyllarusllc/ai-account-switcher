.DEFAULT_GOAL := start

PYTHON ?= python3
NPM ?= npm
PORT ?= 8767
CONFIG ?= profiles.db
PROFILE ?= personal

.PHONY: start dev frontend-deps help test list launch preview

start: ## 启动统一网站（默认操作）
	$(PYTHON) server.py --port "$(PORT)" --config "$(CONFIG)"

dev: frontend-deps ## 安装缺少的前端依赖并启动
	$(MAKE) start

frontend-deps: package.json package-lock.json
	@if [ ! -x node_modules/.bin/tailwindcss ] || [ ! -f node_modules/.dev-deps-ready ] || \
	    [ package.json -nt node_modules/.dev-deps-ready ] || [ package-lock.json -nt node_modules/.dev-deps-ready ]; then \
		command -v "$(NPM)" >/dev/null 2>&1 || { echo '请先安装 Node.js 和 npm。' >&2; exit 1; }; \
		$(NPM) ci --include=dev && touch node_modules/.dev-deps-ready; \
	fi

help: ## 显示常用命令
	@echo 'make                      启动网站，按 Ctrl+C 停止'
	@echo 'make dev                  安装缺少的前端依赖并启动'
	@echo 'make start PORT=9000      使用指定端口'
	@echo 'make start CONFIG=路径    使用指定 SQLite 数据库'
	@echo 'make test                 运行测试'
	@echo 'make list                 列出浏览器配置'
	@echo 'make launch PROFILE=work  启动指定 Chrome 配置'
	@echo 'make preview PROFILE=work 预览启动参数，不启动 Chrome'
	@echo '可用 PYTHON=路径 指定 Python 3.10+ 解释器'

test: ## 运行测试
	$(PYTHON) -m unittest -v

list: ## 列出浏览器配置
	$(PYTHON) chrome_profiles.py --config "$(CONFIG)" list

launch: ## 启动 Chrome 配置，例如 make launch PROFILE=work
	$(PYTHON) chrome_profiles.py --config "$(CONFIG)" launch "$(PROFILE)"

preview: ## 预览 Chrome 参数，例如 make preview PROFILE=work
	$(PYTHON) chrome_profiles.py --config "$(CONFIG)" launch "$(PROFILE)" --dry-run
