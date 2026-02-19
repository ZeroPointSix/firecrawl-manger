# 设计模式分析

## 1. 创建型模式

### 1.1 工厂模式 (Factory Pattern)

**位置**: `app/config.py:185-201`

```python
def load_config() -> tuple[AppConfig, Secrets]:
    defaults = AppConfig().model_dump()
    config_path = Path(os.environ.get("FCAM_CONFIG", "config.yaml"))
    yaml_config = _load_yaml_file(config_path)
    env_config = _env_overrides()

    merged = _deep_merge(defaults, yaml_config)
    merged = _deep_merge(merged, env_config)

    config = AppConfig.model_validate(merged)
    secrets = Secrets(admin_token=admin_token, master_key=master_key)

    return config, secrets
```

**用途**: 创建配置对象，封装复杂的配置加载逻辑

**优点**:
- 隐藏配置加载的复杂性
- 支持多种配置源（YAML、环境变量）
- 配置验证集中化

### 1.2 建造者模式 (Builder Pattern)

**位置**: `app/core/forwarder.py:109-129`

```python
class Forwarder:
    def __init__(
        self,
        *,
        config: AppConfig,
        secrets: Secrets,
        key_pool: KeyPool,
        key_concurrency: ConcurrencyManager,
        key_rate_limiter: TokenBucketRateLimiter | None = None,
        metrics: Metrics | None = None,
        cooldown_store: object | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # 初始化逻辑
```

**用途**: 构建复杂的 Forwarder 对象，支持可选依赖注入

**优点**:
- 支持灵活的对象构建
- 可选参数使用默认值
- 便于测试（可注入 mock 对象）

## 2. 结构型模式

### 2.1 适配器模式 (Adapter Pattern)

**位置**: `app/core/forwarder.py:99-106`

```python
def _to_fastapi_response(upstream: htsponse) -> Response:
    headers: dict[str, str] = {}
    for k, v in upstream.headers.items():
        lk = k.lower()
        if lk in _DROP_RESPONSE_HEADERS:
            continue
        headers[k] = v
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)
```

**用途**: 将 httpx.Response 适配为 FastAPI Response

**优点**:
- 解耦上游 HTTP 客户端和下游响应格式
- 统一响应处理逻辑
- 便于过滤敏感响应头

### 2.2 装饰器模式 (Decorator Pattern)

**位置**: `app/middleware.py:134-182`

```python
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestReEndpoint) -> Response:
        # 前置处理：生成 request_id
        request_id = incoming if (incoming and _is_valid_request_id(incoming)) else _new_request_id()
        request.state.request_id = request_id

        # 调用下一个处理器
        response = await call_next(request)

        # 后置处理：添加响应头、记录日志
        response.headers["X-Request-Id"] = request_id
        return response
```

**用途**: 为请求处理增加额外功能（ID 追踪、日志、错误处理）

**优点**:
- 不修改核心业务逻辑
- 功能可组合、可复用
- 符合开闭原则

### 2.3 代理模式 (Proxy Pattern)

**位置**: `app/core/forwarder.py:150-329`

```python
def forward(
    self,
    *,
    db: Session,
    request_id: str,
    client: Client,
    method: str,
    upstream_path: str,
    json_body: Any | None,
    inbound_headers: dict[str, str],
) -> ForwardResult:
    # 代理转发请求到上游 Firecrawl API
    # 添加认证、重试、限流等功能
```

**用途**: 作为客户端和上游 API 之间的代理，增加额外控制

**优点**:
- 透明地添加认证、限流、重试等功能
- 保护上游 API
- 统一错误处理

## 3. 行为型模式

### 3.1 策略模式 (Strategy Pattern)

**位置**: `app/core/key_pool.py` (推断)

虽然代码未直接展示，但密钥选择逻辑应该使用了策略模式：
- 轮询策略 (Round Robin)
- 最少使用策略 (Least Used)
- 随机策略 (Random)

**用途**: 根据不同策略选择可用的 API 密钥

**优点**:
- 算法可替换
- 易于扩展新策略
- 符合开闭原则

### 3.2 责任链模式 (Chain of Responsibility)

**位置**: `app/middleware.py`

```python
# 中间件链
RequestIdMiddleware
  → FcamErrorMiddleware
    → RequestLimitsMiddleware
      → 业务处理器
```

**用途**: 请求依次通过多个中间件处理

**优点**:
- 解耦请求发送者和接收者
- 动态组合处理链
- 每个中间件职责单一

### 3.3 模板方法模式 (Template Method Pattern)

**位置**: `app/core/forwarder.py:150-329`

```python
def forward(...) -> ForwardResult:
    # 1. 准备阶段
    headers = _sanitized_request_headers(...)

    # 2. 重试循环（模板）
    while upstream_attempts < total_attempts:
        # 2.1 选择密钥
        selected = self._key_pool.select(...)

        # 2.2 限流检查
        allowed, _ = self._key_rate_limiter.allow(...)

        # 2.3 并发控制
        lease = self._key_concurrency.try_acquire(...)

        # 2.4 发送请求
        resp = client_http.request(...)

        # 2.5 错误处理（不同状态码不同处理）
        if resp.status_code == 429:
            self._mark_cooling(...)
        elif resp.status_code in {401, 403}:
            self._disable_key(...)
```

**用途**: 定义请求转发的算法骨架，子步骤可定制

**优点**:
- 复用公共逻辑
- 子步骤可扩展
- 流程清晰

### 3.4 观察者模式 (Observer Pattern)

**位置**: `app/observability/metrics.py` (推断)

虽然代码未完全展示，但指标收集应该使用了观察者模式：
- 事件发生时通知 Metrics 对象
- Metrics 对象记录指标

**用途**: 解耦业务逻辑和指标收集

**优点**:
- 业务代码不依赖具体的指标实现
- 可动态添加/移除观察者
- 符合开闭原则

### 3.5 状态模式 (State Pattern)

**位置**: `app/db/models.py:51` + `app/core/forwarder.py`

API 密钥的状态机：

```
active → cooling → active
  ↓         ↓
disabled  failed
  ↓         ↓
decrypt_failed
```

**状态转换逻辑**:
- `_mark_active()`: 转换为 active
- `_mark_cooling()`: 转换为 cooling
- `_disable_key()`: 转换为 disabled
- `_record_failure()`: 转换为 failed

**优点**:
- 状态转换逻辑清晰
- 易于添加新状态
- 避免大量 if-else

## 4. 并发模式

### 4.1 令牌桶模式 (Token Bucket Pattern)

**位置**: `app/core/rate_limit.py` (推断)

```python
class TokenBucketRateLimiter:
    def allow(self, key: str, rate_limit: int) -> tuple[bool, int | None]:
        # 检查令牌桶是否有可用令牌
        # 如果有，消耗一个令牌并返回 True
        # 如果没有，返回 False 和重试时间
```

**用途**: 实现限流功能

**优点**:
- 平滑限流
- 支持突发流量
- 性能高效

### 4.2 对象池模式 (Object Pool Pattern)

**位置**: `app/core/key_pool.py` (推断)

```python
class KeyPool:
    def select(self, db: Session, config: AppConfig, client_id: int) -> SelectedKey:
        # 从密钥池中选择可用密钥
        # 考虑状态、配额、冷却期等因素
```

**用途**: 管理和复用 API 密钥

**优点**:
- 提高密钥利用率
- 避免频繁创建/销毁
- 统一管理密钥生命周期

### 4.3 租约模式 (Lease Pattern)

**位置**: `app/core/concurrency.py` (推断)

```python
lease = self._key_concurrency.try_acquire(str(key.id), key.max_concurrent)
try:
    # 使用密钥发送请求
    resp = client_http.request(...)
finally:
    lease.release()  # 释放租约
```

**用途**: 控制并发访问

**优点**:
- 自动释放资源
- 防止资源泄漏
- 支持超时机制

## 5. 数据访问模式

### 5.1 仓储模式 (Repository Pattern)

**位置**: `app/db/models.py` + SQLAlchemy 查询

虽然没有显式的 Repository 类，但 SQLAlchemy 的使用体现了仓储模式：

```python
# 查询操作封装在 ORM 中
db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
db.query(Client).filter(Client.is_active.is_(True)).all()
```

**优点**:
- 抽象数据访问逻辑
- 易于切换数据源
- 便于单元测试

### 5.2 工作单元模式 (Unit of Work Pattern)

**位置**: SQLAlchemy Session

```python
try:
    db.add(key)
    db.flush()
    db.commit()
except Exception:
    db.rollback()
    raise
```

**用途**: 管理事务边界

**优点**:
- 保证数据一致性
- 批量操作优化
- 自动回滚

## 6. 反模式检测

### 6.1 God Class (上帝类)

**位置**: `app/api/control_plane.py` (1388 行)

**问题**:
- 单个文件包含所有管理 API
- 职责过多（密钥管理、客户端管理、统计、日志查询等）

**建议**:
- 拆分为多个路由模块（keys.py, clients.py, stats.py, logs.py）
- 提取公共逻辑到服务层

### 6.2 Magic Numbers (魔法数字)

**位置**: 多处

```python
# app/api/control_plane.py:52
if limit > 200:
    raise FcamError(...)

# app/middleware.py:71
if len(text) <= 2000:
    return text
```

**建议**: 定义常量

```python
MAX_QUERY_LIMIT = 200
MAX_ERROR_DETAILS_LENGTH = 2000
```

### 6.3 Long Method (过长方法)

**位置**:
- `app/api/control_plane.py:606-769` (batch_keys, 164 行)
- `app/core/forwarder.py:150-329` (forward, 180 行)

**建议**: 提取子方法，提高可读性

## 总结

项目整体设计模式使用合理，体现了良好的工程实践。主要优点：
- 分层清晰，职责分离
- 使用了多种经典设计模式
- 代码可扩展性好

主要改进方向：
- 拆分过大的类和方法
- 消除魔法数字
- 引入显式的 Repository 和 Service 层
