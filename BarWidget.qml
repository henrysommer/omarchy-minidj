import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "henrysommer.minidj"

  // The launcher ships inside this plugin folder, so no install step is needed.
  readonly property string launcher: decodeURIComponent(Qt.resolvedUrl("bin/minidj-launch").toString().replace("file://", ""))

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰀥"
    slotSize: Style.bar.statusSlot
    tooltipText: "MiniDJ"
    onPressed: Quickshell.execDetached(["bash", root.launcher])
  }
}
