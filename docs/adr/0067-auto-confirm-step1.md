# 项目级 `auto_confirm_step1`：审片闸站立授权与动作间确认跳过

ADR 0041 把 step1→step2 做成阻塞式内容确认 gate，默认仍保留。熟练流水线需要少等人：决定增加项目顶层布尔 `auto_confirm_step1`（默认关）。开启时，`review_status` 在正式 step1 存在且无隔离草稿的前提下直接派生为 `confirmed`（虚拟确认，不写指纹）；关闭后无指纹的集回到待审。quarantine 仍优先阻塞。同开关约定 video-workflow 跳过动作间 AskUserQuestion（失败 / NEED_CONFIRM / quarantine 仍停）。不放宽 draft validation 与造型 NEED_CONFIRM。
