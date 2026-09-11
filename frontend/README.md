# 招聘评估系统前端

React + TypeScript + Vite前端，读取后端ReadModel并提交业务Command，不在浏览器计算能力分或推导招聘状态。

## 启动

```powershell
npm install
npm run dev
```

默认地址：`http://127.0.0.1:5173`。

## 配置

| 变量 | 说明 | 默认值 |
|---|---|---|
| `VITE_API_BASE_URL` | 后端API根地址 | `http://127.0.0.1:8000/api/v1` |

Docker环境由Compose注入该变量。

## 检查

```powershell
npm run check
```

`check`依次检查分层边界、TypeScript类型和生产构建。

## 目录

```text
src/
├─ app/       路由、Provider、守卫和应用壳
├─ pages/     轻量路由装配
├─ modules/   按招聘业务组织契约、API、Hook、组件和Screen
└─ shared/    HTTP、通用Hook、UI和无业务工具
```

开发约定见根目录《招聘评估系统前端架构与交互说明文档》。
