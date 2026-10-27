import { Menu, Tray, nativeImage } from 'electron'
import { trayTitle, type SendingSnapshot } from '../shared/sending-guard'

interface Handlers {
  show: () => void
  pauseAll: () => void
  quit: () => void
}

/** Menu bar item shown while the window is hidden because a campaign is sending: status, Show, Pause all, Quit. */
export class TrayController {
  private tray: Tray | null = null

  constructor(private readonly handlers: Handlers) {}

  get visible(): boolean {
    return this.tray !== null
  }

  update(snapshot: SendingSnapshot): void {
    this.tray ??= this.create()
    const title = trayTitle(snapshot)
    this.tray.setTitle(`Reachout: ${title}`)
    this.tray.setToolTip(`Reachout is ${title.toLowerCase()}`)
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: `Status: ${title}`, enabled: false },
        { type: 'separator' },
        { label: 'Show Reachout', click: this.handlers.show },
        { label: 'Pause all sending', click: this.handlers.pauseAll },
        { type: 'separator' },
        { label: 'Quit Reachout', click: this.handlers.quit },
      ]),
    )
  }

  destroy(): void {
    this.tray?.destroy()
    this.tray = null
  }

  private create(): Tray {
    return new Tray(nativeImage.createEmpty())
  }
}
