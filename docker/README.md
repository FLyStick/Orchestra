# Docker 手动部署说明

本目录提供第二阶段外部服务的容器编排文件。Redis 与 ChromaDB 由你手动部署，Orchestra 应用仍优先在 Conda 环境直接运行，容器只负责基础设施。

本机使用独立版 `docker-compose`，以下命令直接采用该写法；如果使用 Docker Compose V2 插件，将 `docker-compose` 替换为 `docker compose` 即可。

## 文件说明

- `docker-compose.yml`：Redis 7.4-alpine 与 ChromaDB Server 的统一编排。
- `README.md`：手动启动、验证与清理步骤。

## 前置要求

- Docker Desktop（Windows / macOS）或 Docker Engine（Linux）已安装并启动。
- 宿主机端口 `6379`（Redis）与 `8001`（ChromaDB）未被占用。
- 拉取镜像需要可访问 Docker Hub 的网络。

## 启动服务

在项目根目录执行：

```powershell
docker-compose -f docker/docker-compose.yml up -d
```

首次启动会拉取镜像并自动创建数据卷 `orchestra_redis`、`orchestra_chroma`，容器已设置 `unless-stopped` 重启策略。

## 验证

```powershell
docker-compose -f docker/docker-compose.yml ps
docker exec orchestra-redis redis-cli ping

# ChromaDB Server 心跳（较新版本为 v2，旧版本如 404 改用 v1）
Invoke-RestMethod http://127.0.0.1:8001/api/v2/heartbeat
```

预期结果：`redis-cli ping` 返回 `PONG`，ChromaDB 心跳请求成功返回响应对象。

## 与 .env 的对应关系

| 服务 | 容器内地址 | 宿主机地址 | 应用侧 .env |
| --- | --- | --- | --- |
| Redis | `redis:6379` | `127.0.0.1:6379` | `ORCHESTRA_REDIS_URL=redis://127.0.0.1:6379/0`（包 3 启用） |
| ChromaDB | `chroma:8000` | `127.0.0.1:8001` | `ORCHESTRA_CHROMA_HOST=127.0.0.1`、`ORCHESTRA_CHROMA_PORT=8001`（包 2 启用） |

应用在宿主机运行时应使用 `127.0.0.1`；若未来应用也进入 `orchestra-net` 网络，可改用服务名 `redis` / `chroma`。

## 停止与清理

- 停止容器但保留数据：`docker-compose -f docker/docker-compose.yml down`
- 停止并删除数据卷：`docker-compose -f docker/docker-compose.yml down -v`
- 单独删除数据卷：`docker volume rm orchestra_redis orchestra_chroma`

## 常见问题

- 端口冲突：修改 `docker-compose.yml` 中 `ports` 左侧宿主机端口，并同步 `.env` 中对应的 `ORCHESTRA_CHROMA_PORT` 或 `ORCHESTRA_REDIS_URL`。
- ChromaDB 心跳 404：执行 `docker logs orchestra-chroma` 查看启动日志，尝试 `/api/v1/heartbeat`。
- 包 1 不需要 Redis；未部署 Redis 时保持 `ORCHESTRA_WORKFLOW_DRIVER=sqlite`，包 3 再切换 `redis`。
- 本地 Embedding 模型首次加载需要联网下载，网络受限时优先使用 OpenAI 兼容接口；离线模型缓存目录按使用的模型库设置 `HF_HOME` 或 `SENTENCE_TRANSFORMERS_HOME`。
