/** 工作台共用请求流程的领域入口。 */
export * from '@/domain/workbench-boundary'
export {
  getWorkbenchController,
  resetWorkbenchControllers,
  useWorkbench,
  WorkbenchController,
  type RunActionOptions,
  type WorkbenchState,
} from '@/domain/workbench-controller'
