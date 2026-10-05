# 本地内容变体协商服务

基于 HTTP 内容协商语义（RFC 7231 / RFC 4647 风格）实现的本地变体选择 API。
给定一组按顺序排列的内容变体，以及可选的 `Accept`、`Accept-Language` 头值，
服务分别在媒体类型与语言两个轴上匹配、计算质量因子并返回最佳变体。

## 快速开始

```bash
docker compose up --build
```

启动后服务监听 <http://localhost:8000>：

- 接口：`POST http://localhost:8000/negotiate`
- 交互文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI 描述：<http://localhost:8000/openapi.json>

停止：`Ctrl+C`（或 `docker compose down`）。镜像仅包含 Python 标准库、
FastAPI 与 uvicorn，无任何外部依赖或网络请求。

## 请求

`POST /negotiate`，`Content-Type: application/json`：

```json
{
  "variants": [
    {"id": "v1", "media_type": "text/html", "language": "en-US"},
    {"id": "v2", "media_type": "text/plain", "language": "zh-CN"}
  ],
  "accept": "text/html, application/xhtml+xml;q=0.9, */*;q=0.8",
  "accept_language": "zh-CN,zh;q=0.9,en;q=0.8,*;q=0.5"
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `variants` | 是 | 1～30 个变体，按输入顺序排列。每个变体含唯一的 `id`（非空字符串）、具体的 `media_type`（`type/subtype`，不允许通配符）、`language`（具体语言标签，不允许 `*`）。 |
| `accept` | 否 | 等价于 `Accept` 头的值；缺省（或为 `null`）时视为 `*/*`。 |
| `accept_language` | 否 | 等价于 `Accept-Language` 头的值；缺省（或为 `null`）时视为 `*`。 |

### 头值语法

- 头值按逗号拆分为至多 16 个项；出现空项（如 `text/html,,*/*` 或值为空串）即非法。
- 媒体项只支持：
  - 精确类型 `type/subtype`（如 `text/html`）
  - 子类型通配 `type/*`（如 `text/*`）
  - 全通配 `*/*`
- 语言项只支持：具体语言标签（如 `en`、`zh-Hans-CN`）或 `*`。
- 每个项最多带一个 `q` 参数：以 `;` 分隔，`q` 为 0～1、最多三位小数
  （如 `0`、`1`、`0.5`、`0.123`），缺省为 `1`；不支持任何其他参数。
- 比较不区分大小写；返回值中的 `id` 保持原始大小写。
- 逗号/分号两侧允许空白，项内其他位置（如 token 中间）不允许空白。

## 匹配与选择规则

对每个变体，分别在两个轴上选择“最具体的匹配项”：

- 媒体轴：按 精确匹配 > `type/*` > `*/*` 排序；
- 语言轴：按匹配的子标签数量排序（匹配越长越具体，例如
  `zh-Hans-CN` 匹配 `zh-Hans` 为 2 个子标签），`*` 排在最后；
- 同一具体级别有多个命中项时，取头中位置更靠前的项，并读取该项的 `q`；
- 语言前缀匹配必须在子标签边界结束（`en` 是 `en-US` 的前缀，`en` 不是 `english` 的前缀）；
- 具体项的 `q=0` 不得被更宽泛项覆盖：精确匹配到 `q=0` 时直接判定该轴为 0，
  即使同时存在 `*/*;q=1` 或 `*;q=1`。

只有两个轴的质量都**大于 0** 的变体才参与选择。按两轴 `q` 的乘积降序选出
获胜者；乘积相同（使用精确十进制比较）时依次比较：

1. 媒体匹配具体度（精确 > `type/*` > `*/*`）；
2. 语言匹配具体度（匹配子标签数多者优先，`*` 最低）；
3. 变体的输入顺序（靠前优先）。

## 响应

成功（200）：

```json
{
  "id": "v2",
  "accept_q": 0.8,
  "accept_language_q": 1.0,
  "accept_item": 2,
  "accept_language_item": 1
}
```

- `accept_item` / `accept_language_item` 为命中项在对应头中的序号，**从 1 开始**；
- 当对应头缺省（使用合成的 `*/*` 或 `*`，`q=1`）时，序号为 `null`。

无任何可选变体（两轴质量均未同时大于 0）返回 `406 Not Acceptable`：

```json
{"detail": "No variant satisfies the Accept and Accept-Language criteria."}
```

任何非法输入返回 `422 Unprocessable Entity`，错误详情指出变体下标（从 0 开始）
或头名与项序号（从 1 开始），例如：

```json
{
  "detail": {
    "loc": ["header", "accept", 2],
    "msg": "duplicate q parameter"
  }
}
```

```json
{
  "detail": {
    "loc": ["variant", 0, "media_type"],
    "msg": "wildcard media type is not allowed for a variant"
  }
}
```

## 示例

```bash
curl -s http://localhost:8000/negotiate \
  -H 'Content-Type: application/json' \
  -d '{
    "variants": [
      {"id": "html-en", "media_type": "text/html", "language": "en"},
      {"id": "json-en", "media_type": "application/json", "language": "en"}
    ],
    "accept": "application/json;q=0.9, */*;q=0.1",
    "accept_language": "en"
  }'
```

## 本地开发（不使用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试（仅依赖标准库）：

```bash
python -m unittest discover -s tests -v
```

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 应用与 /negotiate 路由
│   └── negotiation.py   # 头解析、匹配与选择的纯逻辑
├── tests/
│   └── test_negotiation.py
├── Dockerfile
├── compose.yaml
└── requirements.txt
```
