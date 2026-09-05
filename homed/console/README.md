# console — Web Surface（:6006）

企业用户的图形入口：`console.py`（FastAPI，3 个 API）+ `console.html`（单页）。

- **聊天**：车道下拉（auto/small/large/cloud），每条回答标注
  「干活的模型 · 端到端延迟 · 生成 tok/s」
- **文件上传**：文本类文件（txt/md/csv/json/代码，≤2MB）注入对话让模型总结/分析
- **路由可视化**：右侧面板实时显示路由器当前策略（正常分诊/各种降级模式）、
  升级事件流（judge 每次升级的理由原文）、累计请求/token 统计
- 顶栏徽章 + 车道红绿灯 5 秒刷新

访问：AutoDL 控制台「自定义服务」直接暴露 6006；或
`ssh -p <端口> -L 6006:127.0.0.1:6006 root@<主机> -N` 后开 http://localhost:6006。

边界：console 只读 router 的 :4000 和 `logs/cluster_mode`，不碰推理进程。
无鉴权（内网/隧道使用），企业部署加反代鉴权在路线图。
