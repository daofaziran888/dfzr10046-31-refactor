/** 客户端请求交接的公共 API 入口，兼容按 api/workbench 寻找共享边界的调用方。 */
export * from '@/domain/workbench-boundary'
export {
  getWorkbenchController,
  resetWorkbenchControllers,
  useWorkbench,
  WorkbenchController,
} from '@/domain/workbench-controller'
