/**
 * 可替换素材统一配置.
 *
 * 静态素材放置在 public/media/ 目录(发布后可通过 /media/xxx 访问),
 * 更换素材时只需替换文件或修改此处的引用, 无需改动组件代码。
 */
/** 素材版本号: 覆盖同名文件后 +1 即可让浏览器立即生效, 避免缓存 */
const VERSION = 4

export const MEDIA = {
  /**
   * 登录页整页背景图(更换背景时直接覆盖 public/media/bg.svg 即可).
   */
  loginBg: `/media/bg.svg?v=${VERSION}`,
} as const
