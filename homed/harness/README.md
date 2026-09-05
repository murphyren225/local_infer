# harness — 用户入口（Pi）

上游选型 [Pi](https://pi.dev/)（零改动），本组件只有一份 provider 模板：
`pi-models.json` → 安装到 `~/.pi/agent/models.json`（run_cluster 自动做），
把 Pi 指向家庭网关 :4000，声明 auto/small/large/cloud 四个"模型"。

用法：`pi --provider home --model auto`；交互中 `/model` 切车道。

边界：本组件不含任何逻辑；换 harness（比如接 Cursor 或自研 App）
= 把 base_url 指向 :4000，别的组件零改动。
