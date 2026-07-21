# ── Backend: phenomicsAgent API (FastAPI) ──

## 环境要求

- Python ≥ 3.12

## 本地开发

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 复制并编辑配置（config.yaml 含密钥，未被 git 追踪）
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入数据库与 LLM 供应商信息

# 启动（开发模式，热重载）
python -m uvicorn backend.main:app --reload --port 8001
```

服务默认监听 `http://localhost:8001`。API 文档：`http://localhost:8001/docs`

## 目录结构

```
backend/
├── main.py              # FastAPI 入口
├── config/              # 配置加载（config.yaml）
├── auth/                # JWT 认证
├── router/              # API 路由（chat / config / services）
├── services/            # 自定义 HTTP 服务注册（services.yaml）
├── providers/           # LLM 供应商适配
├── db/                  # 数据库模型与 CRUD
├── config.yaml          # 本地配置（gitignored）
├── config.example.yaml  # 配置模板
└── services.yaml        # 已注册的自定义服务
```

## Docker 构建与运行

```bash
cd backend

# 构建镜像
docker build -t phenomics-agent-backend .

# 运行（挂载本地 config.yaml 与 services.yaml）
docker run -d \
  --name phenomics-backend \
  -p 8001:8001 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/services.yaml:/app/services.yaml \
  phenomics-agent-backend
```

容器内工作目录为 `/app`，入口：`uvicorn backend.main:app --host 0.0.0.0 --port 8001`
