import { useState } from "react";
import { Check, Edit2, RefreshCw, Save, X } from "lucide-react";
import type { BoundingBox } from "../services/api";

interface FieldData {
  key: string;
  label: string;
  value: unknown;
  confidence: number;
  bbox?: BoundingBox;
}

interface FieldEditorProps {
  fields: FieldData[];
  onFieldUpdate?: (key: string, value: unknown) => void;
  onFieldSelect?: (key: string, bbox?: BoundingBox) => void;
  selectedField?: string;
  savingField?: string | null;
}

function getConfidenceColor(confidence: number): string {
  if (confidence >= 0.85) return "bg-green-100 text-green-800";
  if (confidence >= 0.70) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
}

function getConfidenceLabel(confidence: number): string {
  if (confidence >= 0.85) return "High";
  if (confidence >= 0.70) return "Medium";
  return "Low";
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function FieldRow({
  field,
  onUpdate,
  onSelect,
  isSelected,
  isSaving,
}: {
  field: FieldData;
  onUpdate: (key: string, value: unknown) => void;
  onSelect: (key: string, bbox?: BoundingBox) => void;
  isSelected: boolean;
  isSaving: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(formatValue(field.value));

  const handleSave = () => {
    onUpdate(field.key, editValue);
    setIsEditing(false);
  };

  const handleCancel = () => {
    setEditValue(formatValue(field.value));
    setIsEditing(false);
  };

  return (
    <div
      className={`p-3 border rounded-lg transition-colors ${
        isSelected
          ? "border-blue-500 bg-blue-50"
          : "border-gray-200 bg-white hover:border-gray-300"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-700">{field.label}</span>
            <span
              className={`px-2 py-0.5 text-xs rounded-full ${getConfidenceColor(
                field.confidence
              )}`}
            >
              {getConfidenceLabel(field.confidence)} ({Math.round(field.confidence * 100)}%)
            </span>
          </div>
          <div className="mt-1">
            {isEditing ? (
              <textarea
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                className="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                rows={3}
              />
            ) : (
              <p
                className={`text-sm ${
                  field.value ? "text-gray-900" : "text-gray-400 italic"
                }`}
              >
                {formatValue(field.value)}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {isEditing ? (
            <>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="p-1.5 text-green-600 hover:bg-green-50 rounded disabled:opacity-50"
                title="Save"
                aria-label="Save field"
              >
                {isSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              </button>
              <button
                onClick={handleCancel}
                disabled={isSaving}
                className="p-1.5 text-gray-500 hover:bg-gray-100 rounded disabled:opacity-50"
                title="Cancel"
                aria-label="Cancel"
              >
                <X className="w-4 h-4" />
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setIsEditing(true)}
                className="p-1.5 text-gray-500 hover:bg-gray-100 rounded"
                title="Edit"
              >
                <Edit2 className="w-4 h-4" />
              </button>
              <button
                onClick={() => onSelect(field.key, field.bbox)}
                className="p-1.5 text-blue-600 hover:bg-blue-50 rounded"
                title="Show on document"
              >
                <Check className="w-4 h-4" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function FieldEditor({
  fields,
  onFieldUpdate,
  onFieldSelect,
  selectedField,
  savingField,
}: FieldEditorProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [filterConfidence, setFilterConfidence] = useState<number | null>(null);

  const filteredFields = fields.filter((field) => {
    const matchesSearch =
      searchTerm === "" ||
      field.label.toLowerCase().includes(searchTerm.toLowerCase()) ||
      formatValue(field.value).toLowerCase().includes(searchTerm.toLowerCase());

    const matchesConfidence =
      filterConfidence === null || field.confidence >= filterConfidence;

    return matchesSearch && matchesConfidence;
  });

  // Group fields by confidence
  const lowConfidenceFields = filteredFields.filter(
    (f) => f.confidence < 0.70
  );
  const mediumConfidenceFields = filteredFields.filter(
    (f) => f.confidence >= 0.70 && f.confidence < 0.85
  );
  const highConfidenceFields = filteredFields.filter(
    (f) => f.confidence >= 0.85
  );

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      <div className="p-4 bg-white border-b border-gray-200">
        <h3 className="text-lg font-semibold text-gray-900">Extracted Fields</h3>
        <div className="mt-3 flex gap-2">
          <input
            type="text"
            placeholder="Search fields..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Search fields"
          />
          <select
            value={filterConfidence ?? ""}
            onChange={(e) =>
              setFilterConfidence(e.target.value ? Number(e.target.value) : null)
            }
            className="px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Filter by confidence"
          >
            <option value="">All confidence</option>
            <option value="0.85">High only (≥85%)</option>
            <option value="0.70">Medium+ (≥70%)</option>
            <option value="0.50">Low+ (≥50%)</option>
          </select>
        </div>
      </div>

      {/* Fields list */}
      <div className="flex-1 overflow-auto p-4 space-y-6">
        {lowConfidenceFields.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-red-600 mb-2 flex items-center gap-2">
              <span className="w-2 h-2 bg-red-500 rounded-full"></span>
              Low Confidence ({lowConfidenceFields.length})
            </h4>
            <div className="space-y-2">
              {lowConfidenceFields.map((field) => (
                <FieldRow
                  key={field.key}
                  field={field}
                  onUpdate={onFieldUpdate || (() => {})}
                  onSelect={onFieldSelect || (() => {})}
                  isSelected={selectedField === field.key}
                  isSaving={savingField === field.key}
                />
              ))}
            </div>
          </div>
        )}

        {mediumConfidenceFields.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-yellow-600 mb-2 flex items-center gap-2">
              <span className="w-2 h-2 bg-yellow-500 rounded-full"></span>
              Medium Confidence ({mediumConfidenceFields.length})
            </h4>
            <div className="space-y-2">
              {mediumConfidenceFields.map((field) => (
                <FieldRow
                  key={field.key}
                  field={field}
                  onUpdate={onFieldUpdate || (() => {})}
                  onSelect={onFieldSelect || (() => {})}
                  isSelected={selectedField === field.key}
                  isSaving={savingField === field.key}
                />
              ))}
            </div>
          </div>
        )}

        {highConfidenceFields.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-green-600 mb-2 flex items-center gap-2">
              <span className="w-2 h-2 bg-green-500 rounded-full"></span>
              High Confidence ({highConfidenceFields.length})
            </h4>
            <div className="space-y-2">
              {highConfidenceFields.map((field) => (
                <FieldRow
                  key={field.key}
                  field={field}
                  onUpdate={onFieldUpdate || (() => {})}
                  onSelect={onFieldSelect || (() => {})}
                  isSelected={selectedField === field.key}
                  isSaving={savingField === field.key}
                />
              ))}
            </div>
          </div>
        )}

        {filteredFields.length === 0 && (
          <div className="text-center py-8 text-gray-500">
            No fields found matching your criteria.
          </div>
        )}
      </div>
    </div>
  );
}
