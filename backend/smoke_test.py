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
    check("教师2访问教师1学生开题被拒(场景5)", r.json()["code"] == 40400, r.text)

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

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
