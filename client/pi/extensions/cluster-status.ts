// Pi extension template: one tool that reads the cluster console's /api/status.
// Copy this file to add another internal system: rename the tool, change the URL/params.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const CONSOLE = process.env.HOMED_CONSOLE_URL ?? "http://127.0.0.1:6006";

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "cluster_status",
    label: "Cluster status",
    description: "Current mode of the inference cluster, which nodes are up, and recent escalations.",
    parameters: Type.Object({}),
    async execute() {
      const r = await fetch(`${CONSOLE}/api/status`);
      const s = await r.json();
      const nodes = (s.devices ?? [])
        .map((d: any) => `${d.ok ? "up  " : "down"} ${d.name} — ${d.hw}`)
        .join("\n");
      return {
        content: [{ type: "text", text: `mode: ${s.mode}\n${nodes}\nrequests: ${s.stats?.requests ?? "-"}` }],
        details: s,
      };
    },
  });
}
