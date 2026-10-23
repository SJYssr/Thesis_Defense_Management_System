/** element-plus 替身: 只记录 ElMessage 的调用，供断言「提示只弹一次 / 文案正确」。 */
export const notifyLog = []

export function resetNotifyLog() {
  notifyLog.length = 0
}

function level(name) {
  return (message) => {
    notifyLog.push({ level: name, message: String(message) })
    return { close() {} }
  }
}

export const ElMessage = {
  error: level('error'),
  success: level('success'),
  warning: level('warning'),
  info: level('info'),
}

export const ElMessageBox = { alert: () => Promise.resolve(), confirm: () => Promise.resolve() }

export default { ElMessage, ElMessageBox }
