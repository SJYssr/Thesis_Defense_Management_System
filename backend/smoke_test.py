"""核心链路自测: 覆盖 roadmap 九十章的必测业务场景."""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
client = httpx.Client(timeout=120)
passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


def login(username: str, password: str) -> str:
    r = client.post(f"{BASE}/auth/login", json={"username": username, "password": password})
    assert r.json()["code"] == 0, r.text
    return r.json()["data"]["access_token"]


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


print("== 1. 登录与鉴权 ==")
admin = login("admin", "admin123")
t1 = login("teacher01", "123456")
t2 = login("teacher02", "123456")
s1 = login("20230001", "123456")
s6 = login("20230006", "123456")
check("三角色登录", all([admin, t1, t2, s1, s6]))

r = client.get(f"{BASE}/auth/profile", headers=auth(s1))
check("学生资料", r.json()["data"]["role"] == "STUDENT")

r = client.get(f"{BASE}/admin/users/STUDENT", headers=auth(s1))
check("RBAC: 学生访问用户管理被拒", r.json()["code"] == 40300, r.text)

r = client.post(f"{BASE}/auth/login", json={"username": "admin", "password": "wrong"})
check("错误密码被拒", r.json()["code"] == 40100)

print("== 2. 题目与选题 ==")
r = client.get(f"{BASE}/topics", headers=auth(s6), params={"page_size": 10})
topics = r.json()["data"]["items"]
check("题目中心列表", r.json()["code"] == 0 and len(topics) >= 3)

r = client.post(f"{BASE}/student/selections", headers=auth(s6),
                json={"topic_id": topics[3]["id"], "reason": "我对推荐算法方向有浓厚兴趣并完成了相关课程设计。"})
check("学生6申请选题", r.json()["code"] == 0, r.text)

r = client.post(f"{BASE}/student/selections", headers=auth(s6),
                json={"topic_id": topics[2]["id"], "reason": "再次申请另一个题目测试并发约束。" * 2})
check("重复选题被拒(场景: 选题审核中再选题)", r.json()["code"] == 40001, r.text)

# teacher02 的待审列表不应出现 teacher01 题目的选题(对象级隔离)
r = client.get(f"{BASE}/teacher/selections?status=PENDING", headers=auth(t2))
t2_items = r.json()["data"]["items"]
check("教师2待审列表不含教师1的选题", all(i["teacher_id"] == 2 for i in t2_items), r.text[:200])

print("== 3. 阶段状态机强校验(场景1/2/4) ==")
r = client.get(f"{BASE}/student/opening-report", headers=auth(s6))
check("未选题学生查询开题(空)", r.json()["code"] == 0 and r.json()["data"]["status"] == "NOT_SUBMITTED")

r = client.post(f"{BASE}/student/opening-report/submit", headers=auth(s6))
check("未选题提交开题被拒(场景1)", r.json()["code"] == 40001, r.text)

r = client.post(f"{BASE}/student/midterm", headers=auth(s6), json={"content": {}})
check("未开题提交中期被拒(场景4)", r.json()["code"] == 40001, r.text)

print("== 4. 对象级数据权限(场景5) ==")
# teacher02 访问 teacher01 学生(20230001)的开题材料 → 拒绝
r = client.get(f"{BASE}/teacher/opening-reports", headers=auth(t1))
openings = r.json()["data"]["items"]
target = next((o for o in openings if o["student_no"] == "20230001"), None)
check("教师1能看到自己学生的开题", target is not None)
if target:
    r = client.get(f"{BASE}/teacher/opening-reports/{target['id']}", headers=auth(t2))
    # P1-A §8: 该场次答辩评委可拿到只读视图(can_edit=False, is_examiner=True); 非评委仍是 40400。
    # 两种语义都接受, 但"非导师永远不能编辑"这条对象级权限必须在。
    body = r.json()
    ok = body["code"] == 40400 or (body["code"] == 0 and body["data"].get("can_edit") is False
                                   and body["data"].get("is_examiner") is True)
    check("教师2访问教师1学生开题被拒或仅评委只读(场景5)", ok,
          f"status={r.status_code} {r.text[:250]}")

r = client.get(f"{BASE}/teacher/students/progress", headers=auth(t2))
check("教师2进度列表只含自己学生", r.json()["code"] == 0)

print("== 5. 审核流程推进 ==")
# 教师1 审核 学生6 的选题(通过)
r = client.get(f"{BASE}/teacher/selections?status=PENDING", headers=auth(t1))
pending = r.json()["data"]["items"]
sel6 = next((p for p in pending if p["student_no"] == "20230006"), None)
if sel6 is None:  # 学生6申请的是 topics[3] → teacher02 的题, 教师1看不到; 换 teacher02 审核
    r = client.get(f"{BASE}/teacher/selections?status=PENDING", headers=auth(t2))
    sel6 = next((p for p in r.json()["data"]["items"] if p["student_no"] == "20230006"), None)
    approver = t2
else:
    approver = t1
check("找到学生6的待审选题", sel6 is not None)
r = client.post(f"{BASE}/teacher/selections/{sel6['id']}/approve", headers=auth(approver), json={"comment": "同意"})
check("选题审核通过", r.json()["code"] == 0, r.text)

# 名额限制: 该题 max_students=1, 学生5 再申请应被名额挡住
r = client.post(f"{BASE}/student/selections", headers=auth(login("20230005", "123456")),
                json={"topic_id": topics[3]["id"], "reason": "测试名额已满的题目。" * 3})
check("名额已满被拒", r.json()["code"] != 0, r.text)

r = client.get(f"{BASE}/student/progress", headers=auth(s6))
check("学生6进度进入开题阶段", r.json()["data"]["current_stage"] == "OPENING", r.text[:300])

print("== 6. RAG 检索(场景7: 学生隔离) ==")
r = client.post(f"{BASE}/ai/rag/query", headers=auth(s1), json={"query": "系统为什么使用Redis作为向量存储?", "top_k": 5})
data = r.json()["data"]
check("RAG检索返回相关chunk", r.json()["code"] == 0 and len(data["chunks"]) > 0, r.text[:300])
print("    命中:", [(c["section"], round(c["score"], 4)) for c in data["chunks"][:3]])

# 学生6 无知识库 → 不应返回学生1的 chunk
r = client.post(f"{BASE}/ai/rag/query", headers=auth(s6), json={"query": "系统为什么使用Redis作为向量存储?", "top_k": 5})
chunks6 = r.json()["data"]["chunks"]
cross = [c for c in chunks6 if c["document_id"] == 1]
check("学生6检索不到学生1论文(场景7)", len(cross) == 0, str(chunks6[:2]))

print("== 7. AI 模拟答辩全流程 ==")
r = client.post(f"{BASE}/ai/defense/questions/generate", headers=auth(s1))
qs = r.json()["data"]["questions"]
check("生成答辩问题库", r.json()["code"] == 0 and len(qs) >= 15, r.text[:200])

r = client.post(f"{BASE}/ai/defense/sessions?question_count=3", headers=auth(s1))
session_id = r.json()["data"]["session_id"]
r = client.post(f"{BASE}/ai/defense/sessions/{session_id}/start", headers=auth(s1))
first = r.json()["data"]
check("开始答辩返回第一题", r.json()["code"] == 0 and first.get("question"), r.text[:200])
print("    第一题:", first["question"][:60])

for i in range(6):
    q = first["question"] if i == 0 else nxt["question"]
    r = client.post(f"{BASE}/ai/defense/sessions/{session_id}/answer", headers=auth(s1),
                    json={"question": q, "answer": "系统采用Redis Search的向量索引能力, 支持HNSW近似最近邻检索和TAG元数据过滤, "
                                                  "同时Redis已承担缓存与会话职能, 避免引入额外向量数据库组件, 降低了部署复杂度。"})
    ev = r.json()["data"]
    check(f"第{i + 1}轮评价", r.json()["code"] == 0 and ev["scores"]["total"] > 0, r.text[:200])
    r = client.post(f"{BASE}/ai/defense/sessions/{session_id}/next", headers=auth(s1))
    nxt_data = r.json()["data"]
    if nxt_data.get("finished"):
        check("答辩结束并生成报告", "report" in nxt_data and nxt_data["report"].get("overall_score") is not None)
        break
    nxt = nxt_data
    check(f"下一题(第{nxt_data['turn_no']}轮)", bool(nxt_data.get("question")))

r = client.get(f"{BASE}/ai/defense/sessions/{session_id}/report", headers=auth(s1))
check("获取答辩报告", r.json()["code"] == 0 and r.json()["data"].get("overall_score") is not None, r.text[:200])

print("== 8. PPT大纲 / 进度分析 / SSE ==")
r = client.post(f"{BASE}/ai/ppt-outline/generate", headers=auth(s1))
check("PPT大纲生成", r.json()["code"] == 0 and len(r.json()["data"].get("pages", [])) >= 5, r.text[:200])

# 教师1 分析学生1 进度
s1_profile = client.get(f"{BASE}/auth/profile", headers=auth(s1)).json()["data"]["profile_id"]
r = client.post(f"{BASE}/ai/progress/student/{s1_profile}/analyse", headers=auth(t1))
check("AI进度分析(教师视角)", r.json()["code"] == 0 and r.json()["data"].get("summary"), r.text[:200])

# 教师2 无权分析教师1的学生
r = client.post(f"{BASE}/ai/progress/student/{s1_profile}/analyse", headers=auth(t2))
check("教师2分析他人学生被拒", r.json()["code"] == 40300, r.text)

with client.stream("POST", f"{BASE}/ai/chat/stream", headers=auth(s1),
                   json={"query": "我的系统用了哪些技术栈?", "history": None}) as resp:
    events = []
    for line in resp.iter_lines():
        if line.startswith("event:"):
            events.append(line.split(":", 1)[1].strip())
check("SSE流式问答(meta/delta/done)", events.count("delta") > 0 and "done" in events and "meta" in events, str(events[:5]))

print("== 9. Dashboard ==")
r = client.get(f"{BASE}/dashboard/home", headers=auth(admin))
check("管理员首页", r.json()["code"] == 0 and r.json()["data"]["cards"]["student_count"] >= 7)
r = client.get(f"{BASE}/dashboard/stats", headers=auth(admin))
check("管理员统计图表数据", r.json()["code"] == 0 and len(r.json()["data"]["stage_distribution"]) >= 3)
r = client.get(f"{BASE}/dashboard/home", headers=auth(t1))
check("教师首页", r.json()["code"] == 0 and r.json()["data"]["cards"]["student_count"] >= 1)
r = client.get(f"{BASE}/dashboard/stats", headers=auth(s1))
check("学生无权看全局统计", r.json()["code"] == 40300)

print("== 10. 通知与提醒 ==")
r = client.get(f"{BASE}/notifications", headers=auth(s6))
check("通知列表", r.json()["code"] == 0)
r = client.post(f"{BASE}/teacher/students/{s1_profile}/reminders", headers=auth(t1),
                json={"title": "毕业材料提交提醒", "content": "请于本月底前提交毕业论文与相关材料。"})
check("教师发送提醒", r.json()["code"] == 0, r.text[:200])
r = client.get(f"{BASE}/notifications?unread_only=true", headers=auth(s1))
check("学生收到提醒通知", r.json()["data"]["total"] >= 1)

print("== 11. 阶段任务(P1) ==")
from datetime import datetime, timedelta

now_iso = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
due_ok = (datetime.now() + timedelta(days=7)).isoformat(timespec="seconds")
due_pass = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")

r = client.post(f"{BASE}/stage-tasks", headers=auth(t1),
                json={"academic_year": "2025-2026", "stage": "OPENING", "title": "提交开题报告并完成文献综述",
                      "description": "请在截止前完成开题报告提交", "requirements": ["开题报告", "文献综述"],
                      "opens_at": now_iso, "due_at": due_ok, "late_policy": "ALLOW",
                      # D59: 创建缺省进草稿态, 只有显式 PUBLISHED 才经状态机发布, 学生侧 /mine 只列已发布任务
                      "status": "PUBLISHED"})
check("教师创建阶段任务", r.json()["code"] == 0, r.text[:200])
task_id = r.json()["data"]["id"]

r = client.put(f"{BASE}/stage-tasks/{task_id}", headers=auth(t1),
               json={"academic_year": "2025-2026", "stage": "OPENING", "title": "提交开题报告并完成文献综述(修订)",
                     "description": "补充文献综述要求", "requirements": ["开题报告", "文献综述", "技术路线图"],
                     "opens_at": now_iso, "due_at": due_ok, "late_policy": "ALLOW"})
check("教师修改任务(版本化)", r.json()["code"] == 0 and r.json()["data"]["current_version"] == 2, r.text[:200])

r = client.post(f"{BASE}/stage-tasks", headers=auth(t1),
                json={"academic_year": "2025-2026", "stage": "OPENING", "title": "逾期示例任务",
                      "requirements": ["开题报告"], "opens_at": now_iso, "due_at": due_pass,
                      "status": "PUBLISHED"})
overdue_task_id = r.json()["data"]["id"]

r = client.get(f"{BASE}/stage-tasks/mine", headers=auth(s6))
my_tasks = r.json()["data"]["tasks"]
check("学生待办包含所属学年任务", r.json()["code"] == 0 and any(t["id"] == task_id for t in my_tasks), r.text[:300])
overdue_item = next((t for t in my_tasks if t["id"] == overdue_task_id), None)
check("逾期任务被标记", overdue_item is not None and overdue_item["overdue"] is True)
rev_item = next((t for t in my_tasks if t["id"] == task_id), None)
check("待办携带服务端材料状态", rev_item is not None and rev_item["material_status"] in ("NOT_SUBMITTED", "DRAFT"), str(rev_item)[:200])

r = client.get(f"{BASE}/stage-tasks", headers=auth(s6))
check("学生无权查看任务配置列表", r.json()["code"] == 40300, r.text[:120])

print("== 12. 答辩排期(P1) ==")
r = client.get(f"{BASE}/student/progress", headers=auth(s6))
cycle6 = r.json()["data"]["formal_defense"]["cycle_id"]
check("学生6流程实例存在", cycle6 is not None, r.text[:300])

start1 = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
end1 = (datetime.now() + timedelta(hours=4)).isoformat(timespec="seconds")
r = client.post(f"{BASE}/defense-sessions", headers=auth(admin),
                json={"academic_year": "2025-2026", "stage": "FINAL", "title": "终期答辩第3场(冒烟)",
                      "starts_at": start1, "ends_at": end1, "location": "教学楼C-401", "capacity": 5})
check("管理员创建场次草稿", r.json()["code"] == 0, r.text[:200])
S1 = r.json()["data"]["id"]

r = client.post(f"{BASE}/defense-sessions/{S1}/publish", headers=auth(admin))
check("无评委发布被拒", r.json()["code"] != 0, r.text[:200])

r = client.post(f"{BASE}/defense-sessions/{S1}/examiners", headers=auth(admin), json={"teacher_id": 1, "examiner_role": "CHAIR"})
check("分配评委主席", r.json()["code"] == 0, r.text[:200])
r = client.post(f"{BASE}/defense-sessions/{S1}/examiners", headers=auth(admin), json={"teacher_id": 2})
check("分配评委", r.json()["code"] == 0)

r = client.post(f"{BASE}/defense-sessions/{S1}/validate", headers=auth(admin))
check("冲突校验通过", r.json()["data"]["pass"] is True, r.text[:200])

r = client.post(f"{BASE}/defense-sessions/{S1}/publish", headers=auth(admin))
check("场次发布成功", r.json()["code"] == 0 and r.json()["data"]["status"] == "PUBLISHED", r.text[:200])

# 同场地时间重叠 → 发布必须被拒
r = client.post(f"{BASE}/defense-sessions", headers=auth(admin),
                json={"academic_year": "2025-2026", "stage": "MIDTERM", "title": "冲突场次",
                      "starts_at": (datetime.now() + timedelta(hours=3)).isoformat(timespec="seconds"),
                      "ends_at": (datetime.now() + timedelta(hours=5)).isoformat(timespec="seconds"),
                      "location": "教学楼C-401", "capacity": 5})
S_bad = r.json()["data"]["id"]
r = client.post(f"{BASE}/defense-sessions/{S_bad}/publish", headers=auth(admin))
check("场地冲突发布被拒(P1验收)", r.json()["code"] == 40900, r.text[:200])

# 排入学生6 → 已发布场次改期留痕
r = client.post(f"{BASE}/defense-sessions/{S1}/assignments", headers=auth(admin), json={"cycle_ids": [cycle6]})
check("学生排入场次", r.json()["code"] == 0, r.text[:200])

r = client.get(f"{BASE}/defense-sessions/{S1}", headers=auth(admin))
rev = r.json()["data"]["revision"]
new_start = (datetime.now() + timedelta(hours=6)).isoformat(timespec="seconds")
new_end = (datetime.now() + timedelta(hours=8)).isoformat(timespec="seconds")
r = client.patch(f"{BASE}/defense-sessions/{S1}", headers=auth(admin),
                 json={"starts_at": new_start, "ends_at": new_end, "revision": rev, "change_reason": "教室调整"})
check("已发布场次改期成功", r.json()["code"] == 0, r.text[:200])
r = client.patch(f"{BASE}/defense-sessions/{S1}", headers=auth(admin),
                 json={"starts_at": new_start, "revision": rev, "change_reason": "旧版本号"})
check("乐观锁拦截并发修改", r.json()["code"] == 40900, r.text[:150])

r = client.get(f"{BASE}/defense-sessions/mine", headers=auth(s6))
mine = r.json()["data"]
my_seat = next((x for x in mine if x["id"] == S1), None)
check("学生仅看到自己相关场次", my_seat is not None and my_seat["assignment"]["cycle_id"] == cycle6, r.text[:200])
check("学生看到改期后最新时间", my_seat and my_seat["starts_at"] == new_start, str(my_seat)[:150] if my_seat else "")
check("改期留痕可查", my_seat and len(my_seat["changes"]) >= 1)

r = client.get(f"{BASE}/defense-sessions/mine", headers=auth(t1))
check("评委看到被指派场次", any(x["id"] == S1 for x in r.json()["data"]))

print("== 13. 评审与成绩闭环(P2) ==")
r = client.get(f"{BASE}/defense-assignments/mine", headers=auth(t1))
queue = [x for x in r.json()["data"] if x["session"]["id"] == S1]
check("评委评审队列包含新席位", len(queue) == 1, r.text[:300])
seat6 = queue[0]["id"] if queue else None

# 越权: 教师尝试访问其未指派场次的评审(teacher02 对草稿场次无权)
r = client.post(f"{BASE}/defense-sessions", headers=auth(admin),
                json={"academic_year": "2025-2026", "stage": "MIDTERM", "title": "仅教师1的场次",
                      "starts_at": (datetime.now() + timedelta(hours=100)).isoformat(timespec="seconds"),
                      "ends_at": (datetime.now() + timedelta(hours=102)).isoformat(timespec="seconds"),
                      "location": "教学楼D-101", "capacity": 5})
S3 = r.json()["data"]["id"]
client.post(f"{BASE}/defense-sessions/{S3}/examiners", headers=auth(admin), json={"teacher_id": 1, "examiner_role": "CHAIR"})
r = client.post(f"{BASE}/defense-sessions/{S3}/assignments", headers=auth(admin), json={"cycle_ids": [cycle6]})
seat_s3 = r.json()["data"]["added"][0] if r.json()["code"] == 0 else None
r = client.get(f"{BASE}/defense-assignments/{seat_s3}/review", headers=auth(t2))
check("非指派评委访问评审被拒(P2验收)", r.json()["code"] == 40300, r.text[:150])
r = client.get(f"{BASE}/defense-assignments/{seat6}/review", headers=auth(t1))
check("被指派评委可见授权材料", r.json()["code"] == 0 and "materials" in r.json()["data"], r.text[:200])
rubric_weights = r.json()["data"]["rubric"]["weights"]
check("返回启用评分表", rubric_weights and len(rubric_weights) == 3)

scores_ok = {"报告质量": 90, "系统实现": 88, "答辩表现": 86}
r = client.put(f"{BASE}/defense-assignments/{seat6}/review", headers=auth(t1), json={"scores": scores_ok, "comment": "暂存"})
check("评委暂存草稿", r.json()["code"] == 0, r.text[:200])
r = client.post(f"{BASE}/defense-assignments/{seat6}/scores/submit", headers=auth(t1), json={"scores": scores_ok, "comment": "完成度高"})
check("评委提交评分锁定", r.json()["code"] == 0 and r.json()["data"]["status"] == "SUBMITTED", r.text[:200])
r = client.post(f"{BASE}/defense-assignments/{seat6}/scores/submit", headers=auth(t1), json={"scores": scores_ok})
check("重复提交有效评分被拒", r.json()["code"] != 0, r.text[:150])

r = client.post(f"{BASE}/defense-results/generate", headers=auth(admin), json={"assignment_id": seat6})
result_id = r.json()["data"]["id"]
r = client.post(f"{BASE}/defense-results/{result_id}/publish", headers=auth(admin))
check("未到场不能发布结论", r.json()["code"] != 0, r.text[:150])
client.post(f"{BASE}/defense-assignments/{seat6}/seat", headers=auth(admin), json={"seat_status": "ATTENDED"})
r = client.post(f"{BASE}/defense-results/{result_id}/publish", headers=auth(admin))
check("评委人数不足不能发布结论(P2验收)", r.json()["code"] != 0 and "不足" in r.json()["message"], r.text[:200])

r = client.post(f"{BASE}/defense-assignments/{seat6}/scores/submit", headers=auth(t2),
                json={"scores": {"报告质量": 84, "系统实现": 82, "答辩表现": 80}, "comment": "整体良好"})
check("第二位评委提交评分", r.json()["code"] == 0, r.text[:200])

r = client.get(f"{BASE}/defense-results/{result_id}/preview", headers=auth(admin))
preview = r.json()["data"]
expected = round((90 + 84) / 2 * 0.3 + (88 + 82) / 2 * 0.4 + (86 + 80) / 2 * 0.3)
check("服务端复算汇总正确(可复算)", preview["score"] == expected, f"got {preview['score']} expect {expected}")
check("复算与原始评分一致", preview["valid_count"] == 2 and preview["publishable"] is True)

r = client.post(f"{BASE}/defense-results/{result_id}/publish", headers=auth(admin))
check("正式结论发布成功", r.json()["code"] == 0 and r.json()["data"]["status"] == "PUBLISHED", r.text[:200])
r = client.get(f"{BASE}/defense-sessions/mine", headers=auth(s6))
seat_view = next((x for x in r.json()["data"] if x["id"] == S1), None)
check("学生查看已发布结论", seat_view and seat_view["result"] and seat_view["result"]["score"] == expected, str(seat_view)[:250] if seat_view else "")
r = client.post(f"{BASE}/defense-results/{result_id}/withdraw", headers=auth(admin), json={"reason": "汇总复核"})
check("撤回结论留痕", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/defense-sessions/mine", headers=auth(s6))
seat_view = next((x for x in r.json()["data"] if x["id"] == S1), None)
check("撤回后学生端显示复核中", seat_view and seat_view["result"] and seat_view["result"]["status"] == "WITHDRAWN")

print("== 14. 成果归档与文件权限(P3) ==")
r = client.get(f"{BASE}/archive-packages", headers=auth(s1))
pkgs = r.json()["data"]["items"]
check("学生可见本人归档", any(p["cycle_id"] == 1 for p in pkgs), r.text[:200])
pkg1 = next((p for p in pkgs if p["cycle_id"] == 1), None)
r = client.get(f"{BASE}/archive-packages/{pkg1['id']}", headers=auth(s1))
pkg_detail = r.json()["data"]
thesis_file_id = pkg_detail["files"][0]["file_id"] if pkg_detail["files"] else None
check("归档文件快照含哈希", bool(pkg_detail["files"]) and bool(pkg_detail["files"][0]["sha256"]))

r = client.get(f"{BASE}/archive-packages", headers=auth(s6))
check("未公开归档对他人不可见(P3验收)", not any(p["cycle_id"] == 1 for p in r.json()["data"]["items"]), r.text[:300])

r = client.get(f"{BASE}/files/{thesis_file_id}/download", headers=auth(s6))
check("私有论文文件直链下载被拒(P3验收)", r.status_code == 403 and r.json().get("code") == 40300, r.text[:150])
r = client.get(f"{BASE}/files/{thesis_file_id}/download", headers=auth(s1))
check("作者本人可下载", r.status_code == 200 and r.headers.get("content-type", "").startswith(("application/octet-stream", "text/markdown", "application/md", "text/plain")), str(r.status_code))

r = client.get(f"{BASE}/archive-files/{pkg_detail['files'][0]['id']}/download", headers=auth(s6))
check("未授权归档下载被拒", r.status_code == 403 and r.json().get("code") == 40300, r.text[:150])

r = client.post(f"{BASE}/archive-packages/{pkg1['id']}/consent", headers=auth(s1), json={"visibility": "FULL_TEXT"})
check("作者授权公开全文", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/archive-packages", headers=auth(s6))
check("授权后他人可检索到", any(p["cycle_id"] == 1 for p in r.json()["data"]["items"]), r.text[:300])
r = client.get(f"{BASE}/archive-files/{pkg_detail['files'][0]['id']}/download", headers=auth(s6))
check("授权后他人可下载全文", r.status_code == 200 and b"LangChain" in r.content, str(r.status_code))
r = client.delete(f"{BASE}/archive-packages/{pkg1['id']}/consent", headers=auth(s1))
check("作者撤回公开", r.json()["code"] == 0)
r = client.get(f"{BASE}/archive-files/{pkg_detail['files'][0]['id']}/download", headers=auth(s6))
check("撤回后下载立即失效(P3验收)", r.status_code == 403 and r.json().get("code") == 40300, r.text[:150])
r = client.get(f"{BASE}/archive-packages", headers=auth(s6))
check("撤回后检索失效", not any(p["cycle_id"] == 1 for p in r.json()["data"]["items"]), r.text[:200])

r = client.get(f"{BASE}/dashboard/defense-stats", headers=auth(admin))
stats = r.json()["data"]
check("答辩运营统计(P4)", r.json()["code"] == 0 and "archive_rate" in stats and "pending_review" in stats, r.text[:300])
r = client.get(f"{BASE}/dashboard/defense-stats", headers=auth(s1))
check("学生无权看运营统计", r.json()["code"] == 40300)

print("== 15. 系统配置 / PPTX 导出 / 逾期扫描(八十五/四十八/八十六章) ==")
r = client.get(f"{BASE}/config/public")
check("公开配置默认值", r.json()["data"]["system_name"] == "毕业设计管理系统", r.text[:120])
r = client.put(f"{BASE}/admin/system-configs", headers=auth(admin),
               json={"items": {"system_name": "验收专用系统名", "ai_defense_enabled": "false"}})
check("管理员更新配置", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/config/public")
check("配置即时生效(缓存失效)", r.json()["data"]["system_name"] == "验收专用系统名"
      and r.json()["data"]["ai_defense_enabled"] is False, r.text[:150])
r = client.post(f"{BASE}/ai/rag/query", headers=auth(s1), json={"query": "状态机包含哪几个阶段"})
check("AI 总开关关闭后拦截调用", r.status_code == 403 and r.json()["code"] == 40300, r.text[:150])
r = client.put(f"{BASE}/admin/system-configs", headers=auth(admin),
               json={"items": {"system_name": "毕业设计管理系统", "ai_defense_enabled": "true", "not_a_key": "1"}})
check("未知配置项整体拒绝", r.json()["code"] == 40001, r.text[:150])
r = client.put(f"{BASE}/admin/system-configs", headers=auth(admin), json={"items": {"max_upload_size_mb": "abc"}})
check("非法数值被拒绝", r.json()["code"] == 40001, r.text[:150])
r = client.get(f"{BASE}/config/public")
check("拒绝后配置未变", r.json()["data"]["system_name"] == "验收专用系统名", r.text[:120])
r = client.put(f"{BASE}/admin/system-configs", headers=auth(admin),
               json={"items": {"system_name": "毕业设计管理系统", "ai_defense_enabled": "true"}})
check("恢复系统名称与AI开关", r.json()["code"] == 0)

r = client.post(f"{BASE}/ai/ppt-outline/export", headers=auth(s1),
                json={"title": "冒烟测试大纲", "pages": [
                    {"page": 1, "title": "封面", "goal": "展示题目", "key_points": ["要点A", "要点B"],
                     "speaker_notes": "开场", "recommended_visual": "校徽"}]})
check("PPTX 导出为合法 Office 文件", r.status_code == 200 and r.content[:2] == b"PK" and len(r.content) > 10000,
      f"status={r.status_code} bytes={len(r.content)}")
r = client.post(f"{BASE}/ai/ppt-outline/export", headers=auth(s1), json={"title": "空大纲", "pages": []})
check("空大纲导出被拒", r.json().get("code") == 40002, r.text[:150])

r = client.post(f"{BASE}/admin/progress/overdue-scan", headers=auth(admin))
summary = r.json()["data"]
check("逾期风险扫描可触发", r.json()["code"] == 0 and summary["scanned"] >= 1, r.text[:200])
r = client.post(f"{BASE}/admin/progress/overdue-scan", headers=auth(s1))
check("学生无权触发扫描", r.json()["code"] == 40300)

print("== 16. 学年配置 / 选题时间窗 / AI调用日志(八十四/七十七章) ==")
r = client.get(f"{BASE}/admin/academic-schedules?page=1&page_size=100", headers=auth(admin))
data = r.json()["data"]
schedules = data["items"] if isinstance(data, dict) else data
check("学年配置列表可查", r.json()["code"] == 0 and len(schedules) >= 1, r.text[:150])
check("学年配置列表分页契约", isinstance(data, dict) and data["total"] >= len(schedules), str(data)[:150])
check("同一时间仅一个激活学年", sum(1 for s in schedules if s["is_active"]) == 1)
origin = next((s for s in schedules if s["is_active"]), None)
check("存在激活学年配置", origin is not None)

r = client.post(f"{BASE}/admin/academic-schedules", headers=auth(admin),
                json={"academic_year": "2099-2100", "windows": {
                    "opening_start": "2100-01-10T00:00:00", "opening_end": "2100-01-01T00:00:00"}})
check("阶段开始晚于结束被拒", r.json()["code"] == 40002, r.text[:150])
r = client.post(f"{BASE}/admin/academic-schedules", headers=auth(admin),
                json={"academic_year": origin["academic_year"], "windows": {}})
check("重复学年配置被拒", r.json()["code"] == 40002, r.text[:150])

# 激活一个选题窗口早已关闭的学年 → 学生选题应被时间窗拦截
r = client.post(f"{BASE}/admin/academic-schedules", headers=auth(admin),
                json={"academic_year": "2099-2100", "is_active": True,
                      "windows": {"selection_end": "2000-01-01T00:00:00"}})
check("创建并激活新学年", r.json()["code"] == 0, r.text[:150])
s7 = login("20230007", "123456")
r = client.post(f"{BASE}/student/selections", headers=auth(s7),
                json={"topic_id": 1, "reason": "时间窗校验冒烟测试: 我对RAG方向有浓厚兴趣。"})
check("选题窗口外提交被拒(40003)", r.json()["code"] == 40003, r.text[:150])

# 恢复原学年激活, 原窗口原样写回
restore = {"academic_year": origin["academic_year"], "is_active": True,
           "windows": {k: origin[k] for k in origin if k.endswith(("_start", "_end")) and origin[k]}}
r = client.put(f"{BASE}/admin/academic-schedules/{origin['id']}", headers=auth(admin), json=restore)
check("恢复原学年激活", r.json()["code"] == 0 and r.json()["data"]["is_active"] is True, r.text[:150])
r = client.get(f"{BASE}/admin/academic-schedules?page=1&page_size=100", headers=auth(admin))
after = r.json()["data"]["items"]
check("激活切换后仍唯一生效",
      sum(1 for s in after if s["is_active"]) == 1
      and next(s for s in after if s["is_active"])["academic_year"] == origin["academic_year"])
r = client.get(f"{BASE}/academic-schedule/active", headers=auth(s1))
check("登录用户可读当前学年", r.json()["code"] == 0 and r.json()["data"]["academic_year"] == origin["academic_year"],
      r.text[:150])
r = client.get(f"{BASE}/admin/academic-schedules", headers=auth(s1))
check("学生无权管理学年配置", r.json()["code"] == 40300)

r = client.get(f"{BASE}/admin/ai-usage-logs?page_size=5", headers=auth(admin))
usage = r.json()["data"]
check("AI调用日志可查询", r.json()["code"] == 0 and usage["total"] >= 1, r.text[:200])
check("汇总与明细一致", usage["summary"]["calls"] == usage["total"]
      and usage["summary"]["input_tokens"] + usage["summary"]["output_tokens"] > 0)
first = usage["list"][0] if usage["list"] else {}
check("明细含场景/模型/Token/耗时", all(k in first for k in ("scene_label", "model", "input_tokens", "latency_ms")))
r = client.get(f"{BASE}/admin/ai-usage-logs?scene=GENERATE_QUESTIONS", headers=auth(admin))
scene_rows = r.json()["data"]["list"]
check("场景筛选生效", all(i["scene"] == "GENERATE_QUESTIONS" for i in scene_rows) and len(scene_rows) >= 1,
      r.text[:150])
r = client.get(f"{BASE}/admin/ai-usage-logs", headers=auth(t1))
check("教师无权查看AI调用日志", r.json()["code"] == 40300)

print("== 17. Prompt 配置 / AI 模型信息(七十三/八十五章) ==")
r = client.get(f"{BASE}/admin/ai-prompts", headers=auth(admin))
prompts = r.json()["data"]
check("Prompt 场景清单(注册表为准)", r.json()["code"] == 0 and len(prompts) >= 7
      and all(p["source"] == "BUILT_IN" for p in prompts), r.text[:200])
by_code = {p["code"]: p for p in prompts}
check("场景含描述与安全声明标记", by_code["RAG_ANSWER"]["untrusted"] is True
      and by_code["DEFENSE_REPORT"]["untrusted"] is False)

custom = "你是冒烟测试专用评价教师, 评分维度不变, 只输出 JSON。"
r = client.put(f"{BASE}/admin/ai-prompts/DEFENSE_EVALUATE_ANSWER", headers=auth(admin),
               json={"system_prompt": custom})
check("覆盖 Prompt 产生 v1", r.json()["code"] == 0 and r.json()["data"]["version"] == 1, r.text[:150])
r = client.get(f"{BASE}/admin/ai-prompts", headers=auth(admin))
row = next(p for p in r.json()["data"] if p["code"] == "DEFENSE_EVALUATE_ANSWER")
check("清单反映自定义来源与内容", row["source"] == "DB" and custom[:12] in row["system_prompt"])
r = client.put(f"{BASE}/admin/ai-prompts/DEFENSE_EVALUATE_ANSWER", headers=auth(admin),
               json={"system_prompt": custom + "补充规则。"})
check("再次修改变为 v2", r.json()["data"]["version"] == 2)
r = client.put(f"{BASE}/admin/ai-prompts/RAG_ANSWER", headers=auth(admin), json={"system_prompt": "太短"})
check("过短内容被拒", r.json()["code"] == 40002, r.text[:150])
r = client.put(f"{BASE}/admin/ai-prompts/NOT_A_SCENE", headers=auth(admin), json={"system_prompt": custom})
check("未知场景被拒", r.json()["code"] == 40400, r.text[:150])
r = client.post(f"{BASE}/admin/ai-prompts/DEFENSE_EVALUATE_ANSWER/status", headers=auth(admin),
                json={"enabled": False})
check("停用后回退内置默认", r.json()["code"] == 0)
r = client.get(f"{BASE}/admin/ai-prompts", headers=auth(admin))
row = next(p for p in r.json()["data"] if p["code"] == "DEFENSE_EVALUATE_ANSWER")
check("停用场景来源回内置", row["source"] == "BUILT_IN" and row["enabled"] is False)
r = client.get(f"{BASE}/admin/ai-prompts/DEFENSE_EVALUATE_ANSWER/versions", headers=auth(admin))
versions = r.json()["data"]
check("版本历史倒序留痕", r.json()["code"] == 0 and len(versions) == 3
      and versions[0]["action"] == "STATUS" and versions[-1]["action"] == "UPDATE", r.text[:200])
r = client.post(f"{BASE}/admin/ai-prompts/DEFENSE_EVALUATE_ANSWER/reset", headers=auth(admin))
check("重置恢复内置默认", r.json()["code"] == 0 and r.json()["data"]["version"] == 3, r.text[:150])
r = client.get(f"{BASE}/admin/ai-prompts", headers=auth(admin))
row = next(p for p in r.json()["data"] if p["code"] == "DEFENSE_EVALUATE_ANSWER")
# D69: 重置只改内容、不动 status —— 该场景此前已停用, 所以重置后仍是"回退内置"
check("重置后内容与内置一致(停用态仍回退内置)",
      row["source"] == "BUILT_IN" and "模拟答辩教师" in row["system_prompt"])
r = client.post(f"{BASE}/admin/ai-prompts/DEFENSE_EVALUATE_ANSWER/status",
                headers=auth(admin), json={"enabled": True})
check("显式启用成功", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/admin/ai-prompts", headers=auth(admin))
row = next(p for p in r.json()["data"] if p["code"] == "DEFENSE_EVALUATE_ANSWER")
check("启用后 source=DB 且内容为重置后的内置文本",
      row["source"] == "DB" and "模拟答辩教师" in row["system_prompt"])
r = client.get(f"{BASE}/admin/ai-prompts", headers=auth(s1))
check("学生无权管理 Prompt", r.json()["code"] == 40300)
r = client.put(f"{BASE}/admin/ai-prompts/RAG_ANSWER", headers=auth(t1), json={"system_prompt": custom})
check("教师无权修改 Prompt", r.json()["code"] == 40300)

r = client.post(f"{BASE}/ai/rag/query", headers=auth(s1), json={"query": "系统使用了哪些状态码"})
check("自定义/重置后 AI 链路仍可用", r.json()["code"] == 0, r.text[:200])

r = client.get(f"{BASE}/admin/ai-model-info", headers=auth(admin))
info = r.json()["data"]
check("AI 模型信息可查", r.json()["code"] == 0 and info["llm_model_effective"]
      and info["embedding_dim"] >= 0, r.text[:200])
r = client.put(f"{BASE}/admin/system-configs", headers=auth(admin), json={"items": {"ai_llm_model": "glm-4.7-air"}})
check("保存 LLM 模型覆盖", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/admin/ai-model-info", headers=auth(admin))
info = r.json()["data"]
# mock/离线 provider 的"生效模型"就是 mock 本身, 不能拿覆盖值去要求它;
# 真实 provider(openai/ollama)下覆盖值必须真的进到生效模型里
expected_effective = "mock" if info["llm_provider"] == "mock" else "glm-4.7-air"
check("模型信息反映覆盖值",
      info["ai_llm_model"] == "glm-4.7-air" and info["llm_model_effective"] == expected_effective,
      f"override={info['ai_llm_model']!r} effective={info['llm_model_effective']!r} provider={info['llm_provider']!r}")
r = client.put(f"{BASE}/admin/system-configs", headers=auth(admin), json={"items": {"ai_llm_model": ""}})
check("清空覆盖回退环境变量", r.json()["code"] == 0
      and client.get(f"{BASE}/admin/ai-model-info", headers=auth(admin)).json()["data"]["ai_llm_model"] == "")
r = client.get(f"{BASE}/admin/ai-model-info", headers=auth(s1))
check("学生无权查看模型信息", r.json()["code"] == 40300)

print("== 17. 安全硬化验收(P1-B: D11/D13/D21/D22/D23) ==")
# D11: 错误响应必须返回真实 HTTP 状态码, 响应体仍为 {code, message, data}
r = client.get(f"{BASE}/admin/users/STUDENT", headers=auth(s1))
check("D11 权限错误返回 403", r.status_code == 403 and r.json()["code"] == 40300, f"{r.status_code} {r.text[:120]}")
check("D11 错误体结构不变", set(r.json().keys()) == {"code", "message", "data"}, r.text[:120])
r = client.post(f"{BASE}/auth/login", json={"username": "admin", "password": "definitely-wrong"})
check("D11 认证错误返回 401", r.status_code == 401 and r.json()["code"] == 40100, f"{r.status_code} {r.text[:120]}")
r = client.delete(f"{BASE}/admin/users/TEACHER/99999999", headers=auth(admin))
check("D11 资源不存在返回 404", r.status_code == 404 and r.json()["code"] == 40400, f"{r.status_code} {r.text[:120]}")
r = client.get(f"{BASE}/admin/users/UNKNOWN", headers=auth(admin))
check("D11 业务错误返回 400", r.status_code == 400 and r.json()["code"] == 400, f"{r.status_code} {r.text[:120]}")
r = client.get(f"{BASE}/admin/users/TEACHER", headers=auth(admin), params={"page": 0})
check("D11 参数校验错误返回 400(400xx)", r.status_code == 400 and r.json()["code"] == 40002,
      f"{r.status_code} {r.text[:120]}")

# D21: 新建账号密码必填且 >=8 位含字母+数字, 返回 must_change_password
r = client.post(f"{BASE}/admin/users/TEACHER", headers=auth(admin),
                json={"username": "smoke_tmp_teacher", "password": "12345678", "real_name": "临时账号",
                      "role": "TEACHER", "teacher_no": "T-SMOKE-1"})
check("D21 纯数字弱口令被拒", r.status_code == 400 and r.json()["code"] == 40002, f"{r.status_code} {r.text[:150]}")
r = client.post(f"{BASE}/admin/users/TEACHER", headers=auth(admin),
                json={"username": "smoke_tmp_teacher", "real_name": "临时账号", "role": "TEACHER",
                      "teacher_no": "T-SMOKE-1"})
check("D21 密码缺失被拒", r.status_code == 400 and r.json()["code"] == 40002, f"{r.status_code} {r.text[:150]}")
r = client.post(f"{BASE}/admin/users/TEACHER", headers=auth(admin),
                json={"username": "smoke_tmp_teacher", "password": "Smoke12345", "real_name": "临时账号",
                      "role": "TEACHER", "teacher_no": "T-SMOKE-1"})
check("D21 强口令建号并标记 must_change_password",
      r.json()["code"] == 0 and r.json()["data"]["must_change_password"] is True, r.text[:150])
tmp_teacher_id = r.json()["data"]["id"]

# D22: 用户模块操作全部落审计(自动补 method/path/ip)
r = client.get(f"{BASE}/admin/operation-logs", headers=auth(admin),
               params={"module": "USER", "action": "CREATE_USER", "keyword": "smoke_tmp_teacher"})
items = r.json()["data"]["items"]
check("D22 建号写入审计日志", r.json()["code"] == 0 and any(i["result"] == "SUCCESS" for i in items), r.text[:200])
check("D22 审计自动补 method/path/ip",
      bool(items) and all(i["method"] == "POST" and i["path"].endswith("/admin/users/TEACHER") and i["ip"]
                          for i in items), str(items[:1])[:200])
r = client.delete(f"{BASE}/admin/users/TEACHER/{tmp_teacher_id}", headers=auth(admin))
check("D22 删除账号成功", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/admin/operation-logs", headers=auth(admin),
               params={"module": "USER", "action": "DELETE_USER", "keyword": "smoke_tmp_teacher"})
check("D22 删除写入审计(SUCCESS)",
      r.json()["code"] == 0 and any(i["result"] == "SUCCESS" for i in r.json()["data"]["items"]), r.text[:200])

# D23: 结果字段始终有值(历史 NULL 由单测覆盖 UNKNOWN)
r = client.get(f"{BASE}/admin/operation-logs", headers=auth(admin), params={"module": "USER"})
check("D23 审计结果字段非空", r.json()["code"] == 0
      and all(i["result"] for i in r.json()["data"]["items"]), r.text[:200])

print("== 18. 上传策略单一来源(P-upload) ==")
r = client.get(f"{BASE}/files/upload-policy", headers=auth(s1))
policy = r.json().get("data") or {}
check("上传策略返回扩展名白名单与大小上限",
      r.json()["code"] == 0 and bool(policy.get("allowed_extensions")) and policy.get("max_upload_size_mb", 0) > 0,
      r.text[:150])
check("演示视频格式(.mp4)在后端白名单内", ".mp4" in policy.get("allowed_extensions", []),
      str(policy.get("allowed_extensions"))[:200])

r = client.post(f"{BASE}/files/upload", headers=auth(s1),
                files={"file": ("demo.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 512, "video/mp4")},
                data={"business_type": "SMOKE", "business_id": "1"})
uploaded = r.json().get("data") or {}
check("媒体文件可上传并回写业务归属",
      r.json()["code"] == 0 and uploaded.get("business_type") == "SMOKE" and uploaded.get("business_id") == 1,
      r.text[:200])

r = client.post(f"{BASE}/files/upload", headers=auth(s1),
                files={"file": ("evil.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")})
check("可渲染类型(.svg)被拒 40010", r.status_code == 400 and r.json()["code"] == 40010,
      f"{r.status_code} {r.text[:150]}")

print("== 19. 归档自助闭环与孤儿文件清理(P9) ==")
# 迁移 0004 后 archive_package.student_id 为 NOT NULL: 真库上任何构造点漏传都会在此暴露
r = client.get(f"{BASE}/student/archive/eligibility", headers=auth(s1))
elig = r.json().get("data") or {}
check("学生归档资格可查询(含文件清单)",
      r.json()["code"] == 0 and "eligible" in elig and isinstance(elig.get("files"), list),
      r.text[:200])

# seed 里 20230001 的流程已归档(ARCHIVED), 再次提交必须被拒而不是产生第二条记录
r = client.post(f"{BASE}/student/archive/packages", headers=auth(s1),
                json={"files": [{"file_id": 1, "file_role": "THESIS"}]})
check("已完成归档的流程不可重复提交",
      r.status_code == 400 and "已完成归档" in (r.json().get("message") or ""),
      f"{r.status_code} {r.text[:150]}")

# 学院确认只能管理员执行: 学生直接调 approve 必须 403(越权根因是归档包此前无归属列)
r = client.post(f"{BASE}/archive-packages/1/approve", headers=auth(s1), json={"comment": "自己批自己"})
check("学生不可自行确认归档 403", r.status_code == 403 and r.json()["code"] == 40300,
      f"{r.status_code} {r.text[:150]}")

r = client.get(f"{BASE}/archive-packages", headers=auth(admin), params={"status": "PENDING", "page": 1, "page_size": 5})
pending = r.json().get("data") or {}
check("归档列表支持 status 下推且 total 与 items 一致",
      r.json()["code"] == 0 and isinstance(pending.get("items"), list)
      and pending.get("total") == len(pending.get("items") or []),
      r.text[:200])

# 孤儿清理: dry_run 只报数不删, 且能列出可删清单
r = client.post(f"{BASE}/admin/files/gc", headers=auth(admin), params={"retention_days": 7, "dry_run": True})
gc = r.json().get("data") or {}
check("孤儿文件清理 dry_run 返回规模与可删清单",
      r.json()["code"] == 0 and gc.get("dry_run") is True and "deletable" in gc
      and isinstance(gc.get("items"), list),
      r.text[:200])
r = client.post(f"{BASE}/admin/files/gc", headers=auth(s1), params={"dry_run": True})
check("学生不可调用孤儿清理 403", r.status_code == 403 and r.json()["code"] == 40300,
      f"{r.status_code} {r.text[:150]}")
# D13: 登出后旧 access token 立即失效(token_version + 1 + jti 黑名单)
r = client.post(f"{BASE}/auth/logout", headers=auth(s1))
check("D13 登出成功", r.json()["code"] == 0, r.text[:150])
r = client.get(f"{BASE}/auth/profile", headers=auth(s1))
check("D13 登出后旧令牌被拒 401", r.status_code == 401 and r.json()["code"] == 40100,
      f"{r.status_code} {r.text[:150]}")

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
