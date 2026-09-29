/**
 * 可替换素材统一配置.
 *
 * 静态素材放置在 public/media/ 目录(发布后可通过 /media/xxx 访问),
 * 更换素材时只需替换文件或修改此处的引用, 无需改动组件代码。
 */
/** 素材版本号: 覆盖同名文件后 +1 即可让浏览器立即生效, 避免缓存 */
const VERSION = 3

export const MEDIA = {
  /**
   * 登录页左侧插画背景.
   * - login-bg.png: 设计图裁剪版(画面内自带标题/标签文字)
   * - login-bg.svg: 代码绘制的扁平场景(不含文字, 需配合下方文字层)
   */
  loginBg: `/media/login-bg.png?v=${VERSION}`,

  /**
   * 是否叠加代码文字层(主标题/副标题/功能标签).
   * 当前背景图自带文字 → 关闭; 若换成不含文字的背景图(如 login-bg.svg
   * 或自制场景图), 改为 true 即可自动显示标题与标签。
   */
  loginShowcaseOverlay: false,
} as const
