# after_install_smoke

工作流描述：after_install_smoke

```script
# 工作流: after_install_smoke

---

1. 设置选项 fail_fast=True, retry_max_attempts=3, retry_delay_s=1.0

2. 清空后台进程

3. 回到桌面

4. 确保启动应用 "YOUR_APP_NAME"
   - 等待 1.2秒

5. 关闭弹窗
   - 最多 3 轮

6. 点击文字 "个人中心"

7. 采集信息

---
# 完成
```