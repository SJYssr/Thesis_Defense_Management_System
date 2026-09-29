"""导入全部模型, 保证 Base.metadata.create_all 能建齐所有表."""
from app.models.ai import (  # noqa: F401
    AIConversation,
    AIDefenseQuestion,
    AIDefenseSession,
    AIDefenseTurn,
    AIDocument,
    AIDocumentChunk,
    AIMessage,
    AIPromptTemplate,
    AIUsageLog,
)
from app.models.business import (  # noqa: F401
    FinalDefense,
    FinalDefenseVersion,
    GraduationMaterial,
    GraduationMaterialFile,
    GraduationProcess,
    GraduationSchedule,
    GraduationTopic,
    MidtermReport,
    MidtermReportVersion,
    OpeningReport,
    OpeningReportVersion,
    TopicSelection,
)
from app.models.system import (  # noqa: F401
    Announcement,
    LoginLog,
    Notification,
    OperationLog,
    Reminder,
    ReviewRecord,
    SysFile,
    TopicAttachment,
)
from app.models.user import (  # noqa: F401
    AdminProfile,
    ClassInfo,
    College,
    Major,
    StudentProfile,
    SysUser,
    TeacherProfile,
)
