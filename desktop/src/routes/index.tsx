import { createFileRoute } from "@tanstack/react-router";
import { MenuBar, SessionBar, HubSidebar, StatusBar } from "@/percell/Chrome";
import { TaskPanel } from "@/percell/TaskPanels";
import { ImageViewer } from "@/percell/Viewer";
import { CompanionDock } from "@/percell/Companions";
import { usePerCell } from "@/percell/store";

export const Route = createFileRoute("/")({
  component: Index,
});

function Index() {
  const layout = usePerCell((s) => s.layoutPreset);
  const showCompanion = layout !== "laptop";
  return (
    <div className="h-screen w-screen flex flex-col bg-background text-foreground overflow-hidden">
      <MenuBar />
      <SessionBar />
      <div className="flex-1 flex min-h-0">
        <HubSidebar />
        <TaskPanel />
        <ImageViewer />
        {showCompanion && <CompanionDock />}
      </div>
      <StatusBar />
    </div>
  );
}
