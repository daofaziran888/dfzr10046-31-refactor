/** Vue 组合式入口；具体共享流程保留在 domain 边界中，便于非组件测试。 */
export {
  getWorkbenchController,
  resetWorkbenchControllers,
  useWorkbench,
  WorkbenchController,
  type RunActionOptions,
  type WorkbenchState,
} from '@/domain/workbench-controller'

/** 更贴近“共用请求流程”命名的组合式别名。 */
export { useWorkbench as useWorkbenchRequest } from '@/domain/workbench-controller'
