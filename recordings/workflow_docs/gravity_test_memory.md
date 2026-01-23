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

14. 等待 3 秒

15. 触发GC

16. 保存内存快照 "进入任务中心后dump内存(before)"

17. 等待 2 秒

18. 按键 

19. 等待 5 秒

20. 触发GC

21. 保存内存快照 "返回后dump内存"

22. 对比内存快照

23. 分析内存快照

24. 采集内存信息

25. 分析位图

---
# 完成
```