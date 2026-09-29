# media 素材文件夹

此文件夹存放前端可替换素材(登录页背景图等), Vite 会原样发布到站点根路径 `/media/`。

## 文件说明

| 文件 | 说明 |
| ---- | ---- |
| `login-bg.png` | **当前使用的登录背景图**(设计图裁剪版, 画面内自带标题与标签文字) |
| `login-bg.svg` | 备用背景(代码绘制场景, 不含文字) |
| `login-design-source.png` | 设计原图(完整登录页, 仅作参考) |

## 更换登录页背景图

1. 将新背景图放入本文件夹, 例如 `my-bg.jpg`
2. 修改 `src/config/media.ts`:

   ```ts
   export const MEDIA = {
     loginBg: '/media/my-bg.jpg',
     loginShowcaseOverlay: !hasText, // 新图不含标题文字时设为 true, 页面会自动叠加标题/副标题/功能标签
   }
   ```

或者直接用**同名文件覆盖** `login-bg.png`(支持 png/jpg/webp/svg, 竖构图建议 ≥660×940, 横构图 ≥1200×900)。

> 说明: 设计图裁剪版背景内已包含标题文字, 因此代码文字层默认关闭(`loginShowcaseOverlay: false`);
> 换成无文字背景时打开开关即可恢复标题与功能标签, 无需改图片。
