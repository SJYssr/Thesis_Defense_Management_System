"""LLM 服务抽象: 云端 OpenAI 兼容接口 / 本地 ollama / 显式离线 Mock, 切换只改 .env(roadmap 三/AI).

不静默降级: 没有任何真实模型来源时抛 AIServiceUnavailable(code=50301), 而不是悄悄返回
MockLLM 继续输出模板文本 —— 假模型必须让 /health 与前台一眼看见, 而不是假装一切正常。

来源(provider)与凭据来自"生效配置": 数据库系统设置(ai_llm_*) > 环境变量(settings),
见 app/ai/runtime_config.py —— 管理员在系统设置里填 Base URL / API Key / 模型名后保存即生效,
不需要改 .env, 也不需要重启进程。

- openai: 任意 OpenAI 兼容云端(智谱/DeepSeek/通义/OpenAI/自建), 必须有 API Key
- ollama: 本地或远端 ollama 的 OpenAI 兼容端点({OLLAMA_BASE_URL}/v1 或系统设置里的 Base URL)
- auto  : 有 API Key 走云端, 否则探测 ollama, 都不可用即抛异常
- mock  : 显式启用离线模板拼接(仅供单测与演示, 不是真实模型)
"""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Generator

from app.ai.net import open_url, openai_http_client
from app.ai.runtime_config import resolve_ai_config
from app.core.exceptions import BusinessException

logger = logging.getLogger("app.ai.llm")

# 统一话术: 消息必须能直接指向修复动作(启动 ollama 或配置 key), 前端原样透出
UNAVAILABLE_MESSAGE = "未检测到可用的模型服务: 请启动 ollama(ollama serve) 或配置 LLM_API_KEY"
AI_DISABLED_MESSAGE = "AI 功能已在系统设置中关闭(ai_llm_enabled=false): 如需启用请在 系统设置 → AI 服务 中打开"


class AIServiceUnavailable(BusinessException):
    """没有可用的模型来源(而不是降级成 Mock): code 50301 -> HTTP 503."""

    code = 50301
    message = UNAVAILABLE_MESSAGE


class OllamaProbe:
    """ollama 可达性探测: GET {OLLAMA_BASE_URL}/api/tags, 带 TTL 缓存的线程安全快照.

    探测失败只返回 reachable=False(连 detail), 绝不把网络异常抛给调用方 —— 异常路径由
    LLMFactory/EmbeddingFactory 统一翻译成 AIServiceUnavailable。tests 里替换 _fetch
    即可完全离线地验证 TTL 行为。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._result: dict = self._empty("尚未探测")

    @staticmethod
    def _empty(detail: str) -> dict:
        return {"reachable": False, "models": None, "latency_ms": None, "detail": detail}

    def invalidate(self) -> None:
        with self._lock:
            self._cached_at = 0.0
            self._result = self._empty("缓存已失效")

    def snapshot(self, force: bool = False) -> dict:
        """返回 {reachable, models, latency_ms, detail}; TTL 内复用上次结果."""
        from app.core.config import settings

        ttl = max(0.0, float(settings.ollama_probe_ttl))
        with self._lock:  # 持锁探测: 并发调用只打一次 /api/tags
            fresh = self._cached_at > 0 and (time.monotonic() - self._cached_at) < ttl
            if fresh and not force:
                return dict(self._result)
            try:
                result = self._fetch(settings.ollama_base_url, float(settings.ollama_probe_timeout))
            except Exception as exc:  # noqa: BLE001 — 探测永远不把异常抛给调用方
                result = self._empty(f"探测异常: {type(exc).__name__}: {exc}")
            self._cached_at = time.monotonic()
            self._result = result
            return dict(result)

    @staticmethod
    def _fetch(base_url: str, timeout: float) -> dict:
        url = base_url.rstrip("/") + "/api/tags"
        started = time.monotonic()
        try:
            # 回环地址必须绕系统代理: 否则 127.0.0.1:11434 被代理转发成 502, 本地 ollama 被误判为不可达
            with open_url(url, timeout=max(0.1, timeout)) as resp:  # noqa: S310 — 地址来自配置
                payload = json.loads((resp.read() or b"{}").decode("utf-8", "replace") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {
                "reachable": False, "models": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "detail": f"无法连接 {url}: {type(exc).__name__}: {exc}",
            }
        models = []
        for item in payload.get("models") or []:
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                models.append(name)
        return {
            "reachable": True, "models": models,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "detail": f"{url} 可达, 已安装 {len(models)} 个模型",
        }


_ollama_probe = OllamaProbe()


def probe_ollama(force: bool = False) -> dict:
    """ollama 探测快照(TTL 缓存): 供 LLM/Embedding 工厂与 ai/health 共用同一份事实."""
    try:
        return _ollama_probe.snapshot(force=force)
    except Exception as exc:  # noqa: BLE001 — 探测失败只体现为 reachable=False
        logger.warning("ollama 探测失败: %s", exc)
        return {"reachable": False, "models": None, "latency_ms": None,
                "detail": f"探测异常: {type(exc).__name__}: {exc}"}


def ollama_available() -> bool:
    return bool(probe_ollama().get("reachable"))


def reset_ollama_probe_cache() -> None:
    """清空探测缓存(配置变更/测试用)."""
    _ollama_probe.invalidate()


def ollama_model_present(models: list[str] | None, target: str) -> bool:
    """ollama 模型清单里是否有目标模型(models=None 表示清单未知, 不据此判缺失)."""
    if models is None or not (target or "").strip():
        return True
    target = target.strip()
    base = target.split(":")[0]
    return any(m == target or m == f"{target}:latest" or m.split(":")[0] == base for m in models)


class BaseLLMService(ABC):
    name = "base"

    @abstractmethod
    def complete(self, system: str, user: str, temperature: float = 0.3) -> str:
        """同步补全."""

    @abstractmethod
    def stream(self, system: str, user: str, history: list[dict] | None = None, temperature: float = 0.5) -> Generator[str, None, None]:
        """流式补全, 逐段 yield 文本."""

    def structured(self, system: str, user: str, temperature: float = 0.1) -> dict:
        """要求 JSON 结构化输出: 优先用模型 JSON 模式, 失败则正则提取."""
        raw = self.complete(system, user, temperature)
        return extract_json(raw)


def extract_json(text: str) -> dict:
    """从 LLM 输出中稳健提取 JSON 对象(容忍 markdown 代码块/前后缀文本)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 LLM JSON 输出: {text[:200]}")


class OpenAICompatibleLLM(BaseLLMService):
    """OpenAI 兼容 Chat Completions: 通过 base_url 适配各家模型."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout,
                             http_client=openai_http_client(base_url))
        self.model = model
        self.name = f"openai:{model}"

    @staticmethod
    def _messages(system: str, user: str, history: list[dict] | None):
        messages = [{"role": "system", "content": system}]
        for h in (history or [])[-10:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user})
        return messages

    def complete(self, system: str, user: str, temperature: float = 0.3) -> str:
        resp = self.client.chat.completions.create(
            model=self.model, messages=self._messages(system, user), temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def stream(self, system: str, user: str, history: list[dict] | None = None, temperature: float = 0.5):
        resp = self.client.chat.completions.create(
            model=self.model, messages=self._messages(system, user, history),
            temperature=temperature, stream=True,
        )
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class OllamaLLM(OpenAICompatibleLLM):
    """本地 ollama 的 OpenAI 兼容端点({OLLAMA_BASE_URL}/v1): 复用云端实现, 只换 base_url/key/model.

    ollama 的 /v1 同时提供 chat/completions 与 embeddings, 因此不需要独立协议实现;
    真正决定"能不能用"的是模型是否已 pull, 由 ai/health 单独判定并展示。
    """

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 timeout: float | None = None):
        from app.core.config import settings

        resolved = (model or settings.ollama_llm_model or "").strip()
        super().__init__(
            base_url or settings.ollama_v1_url,
            "ollama",  # ollama 不校验 key, 但 OpenAI SDK 要求非空
            resolved,
            timeout=float(timeout if timeout is not None else settings.llm_timeout),
        )
        self.name = f"ollama:{resolved}"


class MockLLM(BaseLLMService):
    """离线 Mock: 无 API Key 时保证整条链路可演示. 基于 prompt 内嵌的 JSON 上下文生成模板化输出."""

    name = "mock"

    def complete(self, system: str, user: str, temperature: float = 0.3) -> str:
        return self._render(system, user)

    def stream(self, system: str, user: str, history: list[dict] | None = None, temperature: float = 0.5):
        text = self._render(system, user)
        for i in range(0, len(text), 12):
            yield text[i:i + 12]
            time.sleep(0.02)

    def _render(self, system: str, user: str) -> str:
        ctx = self._extract_context(user)

        topic = ctx.get("topic_title") or ctx.get("topic") or ctx.get("学生毕业设计题目") or "本毕业设计"
        chunks = ctx.get("chunks") or ctx.get("reference_chunks") or []
        answer = ctx.get("学生回答") or ctx.get("student_answer") or ctx.get("answer") or ""

        # 场景识别依据 System Prompt 中的 JSON schema 关键词, 分支条件互斥且顺序敏感
        if "accuracy" in system:  # 回答评价
            length = len(answer)
            base = max(60, min(90, 55 + length // 8))
            return json.dumps({
                "accuracy": int(base * 0.35), "logic": int(base * 0.25),
                "clarity": int(base * 0.2), "depth": int(max(50, base - 12) * 0.2),
                "feedback": f"你的回答围绕「{topic}」进行了阐述, 覆盖了主要方面。\n\n"
                            f"**优点**: 表达较为清晰, 提到了与题目相关的内容。\n\n"
                            f"**不足**: 回答深度有限, 建议结合论文中的具体设计与数据进一步展开, "
                            f"例如说明技术选型理由、实现细节或测试结果。\n\n"
                            f"**建议**: 回答时先给出结论, 再分点展开依据, 最后补充实例。",
                "follow_up_needed": length < 120,
                "follow_up_question": f"请进一步说明「{topic}」中该方案的实现细节与遇到的问题是如何解决的?" if length < 120 else None,
            }, ensure_ascii=False)

        if "dimension_scores" in system:  # 答辩报告
            turns = ctx.get("turns") or []
            if not turns:
                turns = [{"total": 78}]
            avg = int(sum(t.get("total", t.get("total_score", 78)) for t in turns) / len(turns))
            return json.dumps({
                "overall_score": avg,
                "dimension_scores": {"基础知识": avg, "系统设计": max(50, avg - 3), "技术理解": max(50, avg - 5),
                                     "表达能力": min(95, avg + 3), "应变能力": max(50, avg - 6)},
                "summary": f"本次模拟答辩共完成 {len(turns)} 个问题的回答, 综合表现{'良好' if avg >= 75 else '一般'}。",
                "strengths": ["对系统整体功能较熟悉", "能围绕题目组织回答"],
                "weaknesses": ["部分技术细节阐述不够深入", "缺少对设计方案取舍的论证"],
                "risky_questions": ["为什么选择当前技术栈而非其他方案?", "系统在高并发场景下的表现如何?"],
                "suggestions": ["重读论文第三章系统设计与第五章测试章节", "为每个核心技术准备一个具体例子"],
            }, ensure_ascii=False)

        if "questions" in system and "问题库" in system:  # 问题生成
            plan = {"BASIC": 3, "TECHNOLOGY": 5, "DESIGN": 4, "IMPLEMENTATION": 4, "INNOVATION": 2, "LIMITATION": 2, "EXTENSION": 2}
            keywords = ["系统整体架构", "核心技术选型", "数据库设计", "关键功能实现", "测试方案", "创新点", "系统不足", "后续扩展"]
            questions = []
            i = 0
            for qt, count in plan.items():
                for n in range(count):
                    kw = keywords[i % len(keywords)]
                    i += 1
                    questions.append({
                        "question": f"请结合「{topic}」介绍{kw}方面的设计与考虑。",
                        "question_type": qt,
                        "difficulty": "MEDIUM",
                        "reference": (chunks[0].get("content", "")[:80] if chunks else "依据论文对应章节内容作答"),
                    })
            return json.dumps({"questions": questions[:22]}, ensure_ascii=False)

        if "follow_up_question" in system and "追问" in system:
            return json.dumps({
                "follow_up_question": f"针对你的回答: 在「{topic}」中为什么采用该方案而不是常见替代方案?",
                "follow_up_needed": False,
            }, ensure_ascii=False)

        if "speaker_notes" in system or "PPT" in system:
            return json.dumps({
                "title": f"{topic} 毕业设计答辩",
                "pages": [
                    {"page": 1, "title": "封面", "goal": "呈现题目与个人信息, 建立第一印象", "key_points": [topic, "姓名 / 指导教师 / 日期"], "speaker_notes": "开场自我介绍", "recommended_visual": "校徽+标题"},
                    {"page": 2, "title": "研究背景与意义", "goal": "说明选题来源与研究价值", "key_points": ["行业现状与痛点", "研究价值"], "speaker_notes": "用1-2个例子说明痛点", "recommended_visual": "背景图"},
                    {"page": 3, "title": "系统总体架构", "goal": "讲清系统分层与组件职责", "key_points": ["前端 Vue3", "后端 FastAPI", "MySQL + Redis"], "speaker_notes": "按调用链讲一遍", "recommended_visual": "架构图"},
                    {"page": 4, "title": "核心功能实现", "goal": "展示核心业务流程的技术方案", "key_points": ["毕业流程状态机", "审核工作流", "进度跟踪"], "speaker_notes": "重点讲状态机设计", "recommended_visual": "流程图"},
                    {"page": 5, "title": "RAG 智能能力", "goal": "解释RAG链路如何服务答辩场景", "key_points": ["文档解析与分块", "向量检索", "AI模拟答辩"], "speaker_notes": "结合检索流程图讲解", "recommended_visual": "RAG流程图"},
                    {"page": 6, "title": "系统测试", "goal": "用数据证明功能与检索质量达标", "key_points": ["功能测试", "检索质量测试"], "speaker_notes": "给出关键指标", "recommended_visual": "表格"},
                    {"page": 7, "title": "总结与展望", "goal": "收束工作并指出改进方向", "key_points": ["完成工作", "不足与改进"], "speaker_notes": "致谢", "recommended_visual": "简洁文字"},
                ],
            }, ensure_ascii=False)

        if "recommended_reminder" in system:
            return json.dumps({
                "risk_level": ctx.get("rule_risk", "NORMAL"),
                "summary": f"学生当前进度由规则引擎计算为 {ctx.get('progress_percent', 0)}%, 材料完整性总体{ctx.get('materials_summary', '一般')}。",
                "problems": ["中期报告的测试部分内容偏少", "材料中数据库设计说明不够完整"],
                "suggestions": ["补充系统测试用例与结果说明", "完善数据库ER图与表结构说明"],
                "recommended_reminder": f"提醒: 请按计划推进当前阶段材料并尽快提交。",
            }, ensure_ascii=False)

        if "standalone_query" in system:
            return json.dumps({"standalone_query": ctx.get("question") or user[:80]}, ensure_ascii=False)

        # 通用 RAG 回答
        ref = ""
        if chunks:
            ref = "\n\n".join(f"- {c.get('content', '')[:100]}" for c in chunks[:3])
        material = ctx.get("materials") or ctx.get("检索上下文") or ""
        return (f"基于检索到的资料, 对问题回答如下:\n\n"
                f"{ref or (str(material)[:400] if material else '') or '当前知识库中未找到直接相关内容, 建议补充材料后重试。'}\n\n"
                f"(离线演示模式: 配置 LLM_API_KEY 后将获得真实大模型回答)")

    @staticmethod
    def _extract_context(user: str) -> dict:
        """从 prompt 的【标签】段落或内嵌 JSON 中提取上下文."""
        ctx: dict = {}
        for label in ("学生毕业设计题目", "答辩问题", "学生回答", "学生问题", "之前问答概要",
                      "规则引擎结果", "学生材料文本", "论文材料节选", "模拟答辩表现摘要", "历史对话"):
            m = re.search(rf"【{label}】\s*(.+?)(?=【|$)", user, re.DOTALL)
            if m:
                text = m.group(1).strip()
                if text.startswith("{"):
                    try:
                        ctx[label] = json.loads(text)
                        continue
                    except json.JSONDecodeError:
                        pass
                ctx[label] = text[:400]
        m = re.search(r"【上下文】\s*(\{.*?\})(?:\s*【|$)", user, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                ctx.update(data)
            except json.JSONDecodeError:
                pass
        # 检索上下文中的 chunk 内容供引用
        chunks = re.findall(r"\[\d+\][^\n]*\n(.{10,200})", user)
        if chunks:
            ctx.setdefault("chunks", [{"content": c.strip()} for c in chunks[:5]])
        return ctx


# D68: 模型覆盖在 system_config 中的键(与 shared/sysconfig.FIELDS 的 ai_llm_model 一致)
OVERRIDE_CONFIG_KEY = "ai_llm_model"


class LLMFactory:
    _instance: BaseLLMService | None = None
    _instance_model: str | None = None
    _instance_key: tuple | None = None  # (来源, 模型): 变了就重建, 防止把旧来源的客户端复用成新来源
    _lock = threading.Lock()

    @staticmethod
    def _model_override(db=None) -> str:
        """LLM 模型名覆盖(roadmap 八十五章/D68): 先读 Redis 缓存, 未命中直接查 SystemConfig.

        旧实现缓存未命中就 return "", 会静默回退环境变量默认模型; 后台任务路径(不经过
        router 的补热逻辑)因此永远拿不到管理端配的模型。单行主键读取成本可忽略, 且
        Redis 故障时同样能读到库里的配置。
        """
        try:
            import json

            from app.db.redis import redis_client
            from app.shared.sysconfig import CACHE_KEY

            cached = redis_client.get(CACHE_KEY)
            if cached:
                return str(json.loads(cached).get("ai_llm_model") or "").strip()
        except Exception:  # noqa: BLE001 — Redis 故障不阻塞, 继续直读数据库
            pass
        return LLMFactory._override_from_db(db)

    @staticmethod
    def _override_from_db(db=None) -> str:
        """直读 system_config 单行(主键查询); 库不可用时返回空串, 由调用方回退环境变量."""
        from sqlalchemy import select

        from app.models.system import SystemConfig

        own_session = db is None
        try:
            if own_session:
                from app.db.mysql import SessionLocal

                db = SessionLocal()
            value = db.execute(
                select(SystemConfig.value).where(SystemConfig.key == OVERRIDE_CONFIG_KEY)
            ).scalar_one_or_none()
            return str(value or "").strip()
        except Exception:  # noqa: BLE001 — 数据库不可用同样退回环境变量默认
            return ""
        finally:
            if own_session and db is not None:
                db.close()

    @staticmethod
    def _provider() -> str:
        from app.core.config import settings

        return (settings.llm_provider or "auto").strip().lower()

    @classmethod
    def _cached(cls, key: tuple, build) -> BaseLLMService:
        """按 (来源, 模型) 缓存实例; 来源或模型变了就重建, 不跨来源复用旧客户端."""
        with cls._lock:
            if cls._instance is None or cls._instance_key != key:
                cls._instance = build()
                cls._instance_key = key
                cls._instance_model = key[1]
            return cls._instance

    @staticmethod
    def _require_key(key: str) -> str:
        """云端来源必须有 key: 缺 key 属于配置错误, 必须显式失败而不是降级 Mock."""
        if not (key or "").strip():
            raise AIServiceUnavailable(
                f"{UNAVAILABLE_MESSAGE}(云端来源未配置 API Key: "
                "请在系统设置中填写 API Key, 或设置环境变量 LLM_API_KEY)"
            )
        return key.strip()

    @classmethod
    def _build_openai(cls, cfg) -> BaseLLMService:
        from app.core.config import settings

        key = cls._require_key(cfg.llm_api_key)
        return OpenAICompatibleLLM(cfg.llm_base_url, key, cfg.llm_model,
                                   timeout=float(settings.llm_timeout))

    @classmethod
    def _build_ollama(cls, cfg) -> BaseLLMService:
        return OllamaLLM(cfg.ollama_llm_model, cfg.ollama_llm_base_url)

    @classmethod
    def get(cls, db=None) -> BaseLLMService:
        """取当前 LLM 实现(配置: 系统设置 > 环境变量); 无可用来源抛 AIServiceUnavailable."""
        cfg = resolve_ai_config(db)
        if not cfg.llm_enabled:
            raise AIServiceUnavailable(AI_DISABLED_MESSAGE)

        provider = cfg.llm_provider
        if provider == "mock":
            logger.warning("LLM_PROVIDER=mock: 使用离线模板拼接, 输出不是真实模型回答")
            return cls._cached(("mock", None), MockLLM)
        if provider == "openai":
            # 先判配置, 不要为了一次注定失败的调用去读模型覆盖/建客户端
            key = cls._require_key(cfg.llm_api_key)
            return cls._cached(("openai", cfg.llm_base_url, cfg.llm_model, key),
                               lambda: cls._build_openai(cfg))
        if provider == "ollama":
            # 显式指定 ollama 时不做可达性探测: 不可达会在调用时报错, 由 /health 展示
            return cls._cached(("ollama", cfg.ollama_llm_base_url, cfg.ollama_llm_model),
                               lambda: cls._build_ollama(cfg))
        if provider != "auto":
            raise AIServiceUnavailable(f"{UNAVAILABLE_MESSAGE}(未知 LLM_PROVIDER={provider})")

        # auto: 云端密钥优先, 其次 ollama, 都没有就显式失败
        if (cfg.llm_api_key or "").strip():
            key = cls._require_key(cfg.llm_api_key)
            return cls._cached(("openai", cfg.llm_base_url, cfg.llm_model, key),
                               lambda: cls._build_openai(cfg))
        probe = probe_ollama()
        if probe.get("reachable"):
            return cls._cached(("ollama", cfg.ollama_llm_base_url, cfg.ollama_llm_model),
                               lambda: cls._build_ollama(cfg))
        raise AIServiceUnavailable(
            f"{UNAVAILABLE_MESSAGE}({probe.get('detail') or 'ollama 不可达'})"
        )

    @classmethod
    def provider_name(cls) -> str:
        """展示用来源名: 不可用时是 "unavailable", 不再谎报 "mock"."""
        try:
            return cls.get().name
        except Exception as exc:  # noqa: BLE001 — 展示字段不允许把 /health 打成异常
            logger.warning("LLM 不可用: %s", exc)
            return "unavailable"

    @classmethod
    def reset(cls) -> None:
        """清空实例缓存(配置热更新/测试用)."""
        with cls._lock:
            cls._instance = None
            cls._instance_key = None
            cls._instance_model = None
