# Gravity_清进程_回桌面_找图进入个人中心_找图进入任务中心_全程录屏_任务中心找广告去完成

工作流描述：Gravity_清进程_回桌面_找图进入个人中心_找图进入任务中心_全程录屏_任务中心找广告去完成

```script
# 工作流: Gravity_清进程_回桌面_找图进入个人中心_找图进入任务中心_全程录屏_任务中心找广告去完成

---

1. 设置选项 fail_fast=True, retry_max_attempts=3, retry_delay_s=1

2. 清空后台进程

3. 等待 0.6 秒

4. 回到桌面

5. 等待 1 秒

6. 从桌面启动应用 "Gravity"
   - 等待 1.2秒

7. 等待 2 秒

8. 点击文字 "anonymous.sns.community.gravity:id/tab_center"

9. 等待 1 秒

10. 点击文字 "anonymous.sns.community.gravity:id/clPoint"

11. 等待 1.2 秒

12. 采集信息

13. 断言 last.focused_activity == 'com.hiclub.android.gravity.point.TaskCenterActivity'
   - 错误提示: 未检测到进入任务中心页面：focused_activity 不是 TaskCenterActivity

14. 点击文字 "ディリータスク"

15. 保存内存快照 "点击已完成后dump内存"

16. 等待 2 秒

17. 点击文字 "広告を最後まで見る"
   - 偏移: 向右508像素, 向下56像素

18. 等待 3 秒

19. 等待 33 秒

20. 截图 "等待33秒后截图"

21. 点击文字 "close"

22. 等待 2 秒

23. 保存内存快照 "点击close后dump内存"

24. 对比内存快照

25. 分析内存快照

---
# 完成
```