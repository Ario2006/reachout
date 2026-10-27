import { Menu, type MenuItemConstructorOptions, app } from 'electron'
import type { MenuAction } from '../shared/bridge'
import { GO_ROUTES } from '../shared/go-routes'

interface MenuDeps {
  isDev: boolean
  send: (action: MenuAction) => void
  showLogs: () => void
  openDataFolder: () => void
}

export function buildMenu({ isDev, send, showLogs, openDataFolder }: MenuDeps): Menu {
  const template: MenuItemConstructorOptions[] = [
    {
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { label: 'Settings…', accelerator: 'Cmd+,', click: () => send({ type: 'navigate', route: '/settings' }) },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Command Palette…', accelerator: 'Cmd+K', click: () => send({ type: 'command-palette' }) },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        ...(isDev
          ? ([{ type: 'separator' }, { role: 'reload' }, { role: 'toggleDevTools' }] as MenuItemConstructorOptions[])
          : []),
      ],
    },
    {
      label: 'Go',
      submenu: GO_ROUTES.map((g, i) => ({ label: g.label, accelerator: `Cmd+${i + 1}`, click: () => send({ type: 'navigate', route: g.route }) })),
    },
    { role: 'windowMenu' },
    {
      label: 'Help',
      submenu: [
        { label: 'Show Logs', click: showLogs },
        { label: 'Open Data Folder', click: openDataFolder },
      ],
    },
  ]
  return Menu.buildFromTemplate(template)
}
