export function StatusBar({ health, validationMessage, hash, gpu, stepCount, stepLimit, elapsed, runId, warnings }: {
  health: 'checking' | 'ready' | 'offline'; validationMessage: string; hash: string; gpu: string;
  stepCount: number; stepLimit: number | null; elapsed: string; runId: string; warnings: string[];
}) {
  return <footer className="status-bar">
    <span className={`status-service ${health}`}><i/> {health === 'ready' ? '本机服务已连接' : health === 'checking' ? '正在连接' : '本机服务离线'}</span>
    <details className="status-validation"><summary>校验 {hash ? '✓' : '…'}</summary><div><strong>{validationMessage || '等待工作台校验…'}</strong>{warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div></details>
    <span>GPU {gpu}</span><span>步骤 {stepCount}/{stepLimit ?? '不限'}</span><span>{elapsed}</span>{runId && <code>RUN {runId.slice(0, 12)}</code>}
  </footer>;
}
