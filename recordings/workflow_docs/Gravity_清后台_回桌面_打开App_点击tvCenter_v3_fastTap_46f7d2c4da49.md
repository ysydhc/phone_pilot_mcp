# Gravity_清后台_回桌面_打开App_点击tvCenter_v3_fastTap

提速版：点击 tvCenter 关闭 find_and_tap 的 before/after 截图，并移除 verify（verify 会额外做多次 UI dump 轮询）。保留点击后单独 screenshot 作为证据。

```script
# 工作流: Gravity_清后台_回桌面_打开App_点击tvCenter_v3_fastTap

---

1. 设置选项 fail_fast=True, retry_max_attempts=2, retry_delay_s=0.4

2. 清空后台进程

3. 回到桌面

4. 确保启动应用 "Gravity"
   - 等待 1.5秒

5. 等待页面 ""

6. 关闭弹窗
   - 最多 2 轮

7. 等待出现 "anonymous.sns.community.gravity:id/tvCenter"
   - 超时: 10秒

8. 点击文字 "anonymous.sns.community.gravity:id/tvCenter"

9. 截图 "after_tap_tvCenter"

---
# 完成
```