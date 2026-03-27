import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2, Plus, Webhook } from "lucide-react";
import toast from "react-hot-toast";
import { webhooksApi, authApi } from "../services/api";

// Must match backend WEBHOOK_EVENTS in backend/api/routes/webhooks.py
const AVAILABLE_EVENTS = [
  "document.pending",
  "document.preprocessing",
  "document.ocr_started",
  "document.classified",
  "document.structuring",
  "document.reconciliation",
  "document.validating",
  "document.completed",
  "document.review",
  "document.failed",
];

export function Settings() {
  const queryClient = useQueryClient();

  const { data: account } = useQuery({
    queryKey: ["auth-me"],
    queryFn: () => authApi.me(),
  });

  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>(["document.completed"]);
  const [showForm, setShowForm] = useState(false);

  const { data: webhooks = [], isLoading } = useQuery({
    queryKey: ["webhooks"],
    queryFn: () => webhooksApi.list(),
  });

  const createMutation = useMutation({
    mutationFn: () => webhooksApi.create({ name, url, events: selectedEvents }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webhooks"] });
      toast.success("Webhook created");
      setName("");
      setUrl("");
      setSelectedEvents(["document.completed"]);
      setShowForm(false);
    },
    onError: () => toast.error("Failed to create webhook"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => webhooksApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webhooks"] });
      toast.success("Webhook deleted");
    },
    onError: () => toast.error("Failed to delete webhook"),
  });

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]
    );
  };

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Settings</h1>

      {/* Account Info */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Account</h2>
        {account ? (
          <div className="space-y-3">
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Name</p>
              <p className="mt-1 text-sm text-gray-900">{account.name}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Email</p>
              <p className="mt-1 text-sm text-gray-900">{account.email}</p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">Loading account information...</p>
        )}
      </div>

      {/* Webhooks */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Webhook className="w-5 h-5 text-gray-600" />
            <h2 className="text-lg font-semibold text-gray-900">Webhooks</h2>
          </div>
          <button
            onClick={() => setShowForm((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Plus className="w-4 h-4" />
            Add Webhook
          </button>
        </div>

        {showForm && (
          <div className="mb-6 p-4 bg-gray-50 rounded-lg border border-gray-200 space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My webhook"
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">URL</label>
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/webhook"
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-2">Events</label>
              <div className="flex flex-wrap gap-2">
                {AVAILABLE_EVENTS.map((event) => (
                  <label key={event} className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event)}
                      onChange={() => toggleEvent(event)}
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="text-xs text-gray-700">{event}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => createMutation.mutate()}
                disabled={!name || !url || selectedEvents.length === 0 || createMutation.isPending}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {createMutation.isPending ? "Creating..." : "Create"}
              </button>
              <button
                onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {isLoading ? (
          <p className="text-sm text-gray-500 py-4 text-center">Loading webhooks...</p>
        ) : webhooks.length === 0 ? (
          <p className="text-sm text-gray-500 py-4 text-center">
            No webhooks configured. Add one to receive event notifications.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {webhooks.map((wh) => (
              <li key={wh.id} className="py-3 flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900">{wh.name}</p>
                  <p className="text-xs text-gray-500 truncate">{wh.url}</p>
                  {wh.events && wh.events.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {wh.events.map((e: string) => (
                        <span key={e} className="px-1.5 py-0.5 text-xs bg-blue-50 text-blue-700 rounded">
                          {e}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  onClick={() => deleteMutation.mutate(wh.id)}
                  disabled={deleteMutation.isPending}
                  className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors flex-shrink-0"
                  title="Delete webhook"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
