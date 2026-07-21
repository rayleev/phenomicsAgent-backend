# ── Backend Dockerfile ──
# 构建产物镜像，运行 FastAPI 服务
# 使用方式见 backend/README.md

FROM python:3.12-slim

WORKDIR /app

# 安装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端源码
COPY . .

# 暴露端口
EXPOSE 8001

# 启动命令（生产模式，单 worker；生产环境可用 gunicorn + uvicorn workers）
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]
