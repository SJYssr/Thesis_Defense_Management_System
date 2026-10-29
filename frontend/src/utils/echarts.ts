/**
 * ECharts 按需注册的唯一入口。
 *
 * 原来 AdminHome / AdminProgress / AdminStatistics / TeacherHome / AiProgress 五处各自
 * `import * as echarts from 'echarts'`，整包（约 1.09 MB）被打进同一个 echarts chunk。
 * 实际只用到 bar / line / pie 三种图型、grid / tooltip / legend / title 四个组件和 Canvas
 * 渲染器，全量引入等于为二十多种没用到的图型和四种用不到的坐标系付流量。
 *
 * 新增图型必须回到这里补注册，否则运行时才报 "Component xxx not exists" —— 这是按需
 * 引入的代价，也是它能瘦下来的原因。
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer,
])

export default echarts
export type { ECharts } from 'echarts/core'
