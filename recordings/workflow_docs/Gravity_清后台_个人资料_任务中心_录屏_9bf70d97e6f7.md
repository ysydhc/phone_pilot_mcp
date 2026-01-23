# Gravity_清后台_个人资料_任务中心_录屏

清后台 → 打开 Gravity → 图像点击个人资料 → 图像点击任务中心，全程录屏；点击步骤带重试校验。

```script
# 工作流: Gravity_清后台_个人资料_任务中心_录屏

---

1. 设置选项 fail_fast=True, retry_max_attempts=3, retry_delay_s=1

2. 清空后台进程

3. 确保启动应用 "Gravity"
   - 等待 1.2秒

4. 等待 1 秒

5. 点击图片 @templates/gravity_个人资料_4765a2b3ab23.png
   - 相似度: 85%

6. 等待 1 秒

7. 点击图片 @templates/gravity_任务中心_a33aa7282070.png
   - 相似度: 85%

8. 等待 1.2 秒

9. 采集信息

10. 断言 'TaskCenter' in (last.focused_activity or '')
   - 错误提示: 未检测到进入任务中心页面：focused_activity 不包含 'TaskCenter'

---
# 完成
```