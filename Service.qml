import QtQuick
import Quickshell
import Quickshell.Io

// OmaWeb service: keeps the theme wiring in place. The actual CSS is compiled
// by bin/omaweb into the OmaWeb asset extension, and Stylus imports it once
// through chrome-extension://, so a theme switch never needs a re-import.
//
// This service only (re)installs wiring once per shell start. It deliberately
// does not poll or reapply on a timer.
Item {
  id: root
  visible: false

  property var shell: null
  property var manifest: null

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/omaweb"
  readonly property string pluginDir: manifest && manifest.__sourceDir
    ? manifest.__sourceDir
    : home + "/.config/omarchy/plugins/io.github.alxwolfenstein97.omaweb"

  property var state: null
  property string lastError: ""

  readonly property string themeName: state && state.theme ? String(state.theme) : ""
  readonly property string mode: state && state.mode ? String(state.mode) : ""
  readonly property int enabledStyles: state && state.counts && state.counts.enabled
    ? Number(state.counts.enabled) : 0
  readonly property int totalStyles: state && state.counts && state.counts.total
    ? Number(state.counts.total) : 0

  FileView {
    path: root.stateDir + "/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parseState(text())
    onLoadFailed: root.state = null
  }

  function parseState(content) {
    try {
      var parsed = JSON.parse(String(content || ""))
      root.state = parsed && typeof parsed === "object" ? parsed : null
    } catch (e) {
      console.warn("omaweb", "Ignoring bad state.json", e)
      root.state = null
    }
  }

  Process {
    id: installer
    running: false
    command: ["bash", root.pluginDir + "/install.sh", "--quiet"]
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var message = String(text || "").trim()
        if (message.length > 0)
          root.lastError = message.length > 400 ? message.slice(-400) : message
      }
    }
    onExited: function (exitCode) {
      if (exitCode === 0)
        return
      console.warn("omaweb: installer exited " + exitCode
                   + (root.lastError.length > 0 ? ": " + root.lastError : ""))
    }
  }

  Timer {
    interval: 2000
    running: true
    repeat: false
    onTriggered: {
      if (!installer.running)
        installer.running = true
    }
  }
}
