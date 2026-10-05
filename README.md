# Content Variant Negotiation API

一个本地内容变体协商服务（Python / FastAPI）。给定一组内容变体与可选的
`Accept` / `Accept-Language` 头值，按 HTTP 内容协商规则选出最佳变体。

## 快速开始

```bash
docker compose up --build
```

服务随即在 <http://localhost:8000> 可用（交互式文档见
<http://localhost:8000/docs>）。无需任何外部依赖。

本地开发（不用 Docker）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

运行测试：

```bash
pip install pytest httpx
pytest tests/
```

## API

### `POST /negotiate`

请求体：

```json
{
  "variants": [
    {"id": "v1", "media_type": "text/html", "language": "en-US"},
    {"id": "v2", "media_type": "application/json", "language": "en"}
  ],
  "accept": "text/*;q=0.8, application/json;q=0.9",
  "accept_language": "en;q=0.7, fr;q=0.9"
}
```

- `variants`：必填，1～30 个，按顺序排列。每个变体含唯一的 `id`、具体的
  `media_type`（`type/subtype`，不含通配符）和 `language`（语言标签）。
- `accept` / `accept_language`：可缺省的头值字符串，缺省分别视为
  `*/*` 与 `*`（q=1）。

成功响应 `200`：

```json
{
  "id": "v2",
  "media_q": 0.9,
  "language_q": 0.7,
  "media_index": 1,
  "language_index": 0
}
```

`media_index` / `language_index` 是命中项在对应头中的序号（从 0 开始）；
头缺省时为 `null`。

### 头值语法

- 按逗号拆分，每个头至多 16 项；空项（如 `en,,fr`、尾部逗号、空字符串）报错。
- 媒体项仅支持 `type/subtype`、`type/*`、`*/*`；语言项仅支持语言标签前缀或 `*`。
- 每项可带唯一 `q` 参数（如 `;q=0.85`，0～1、最多三位小数，缺省为 1），
  不支持其他参数；参数名不区分大小写。
- 匹配不区分大小写；语言前缀必须在子标签边界结束（`en` 匹配 `en-US`，
  `en-U` 不匹配 `en-US`）。

### 协商规则

1. 对每个变体，在两个轴上分别取**最具体**的匹配项：
   - 媒体：精确 > `type/*` > `*/*`；
   - 语言：按匹配的子标签数排序，`*` 最后。
   - 同级取头中靠前的一项，并读取该项的 q。因此具体项的 `q=0`
     不会被宽泛项（如 `*/*;q=1`）覆盖。
2. 仅保留两轴 q 均大于 0 的变体（某轴无任何匹配项视为 q=0）。
3. 按 q 乘积降序选中；同分依次比较媒体具体度、语言具体度、变体输入顺序。

### 错误响应

- `406 Not Acceptable`：没有可接受的变体。

  ```json
  {"detail": {"error": "no_acceptable_variant", "message": "..."}}
  ```

- `422 Unprocessable Entity`：非法输入。变体问题指出 `variant_index`，
  头值问题指出 `header` 与 `item_index`（均从 0 开始）：

  ```json
  {"detail": {"error": "invalid_variant", "variant_index": 2, "message": "..."}}
  ```

  ```json
  {"detail": {"error": "invalid_header", "header": "accept", "item_index": 1, "message": "empty item"}}
  ```

### `GET /healthz`

健康检查，返回 `{"status": "ok"}`。

## 项目结构

```
app/
  main.py          # FastAPI 应用与请求校验
  negotiation.py   # 头解析、匹配与选择逻辑（纯函数，可独立测试）
tests/
  test_negotiation.py
Dockerfile
compose.yaml
requirements.txt
```
