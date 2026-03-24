import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { documentsApi } from "../services/api";
import toast from "react-hot-toast";
import { Upload as UploadIcon } from "lucide-react";

export function Upload() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile) setFile(droppedFile);
  }, []);

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);

    try {
      const doc = await documentsApi.upload(file);
      toast.success("Document uploaded successfully!");
      navigate(`/documents/${doc.id}`);
    } catch {
      toast.error("Upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Upload Document</h1>

      <div
        className="border-2 border-dashed border-gray-300 rounded-lg p-12 text-center hover:border-blue-500 transition-colors"
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
      >
        <UploadIcon className="mx-auto h-12 w-12 text-gray-400" />
        <h3 className="mt-4 text-lg font-medium text-gray-900">
          {file ? file.name : "Drop a file here or click to browse"}
        </h3>
        <p className="mt-2 text-sm text-gray-500">
          Supports PDF, PNG, JPG, TIFF (max 50MB)
        </p>
        <input
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          className="hidden"
          id="file-input"
        />
        <label
          htmlFor="file-input"
          className="mt-4 inline-block px-4 py-2 bg-blue-600 text-white rounded-lg cursor-pointer hover:bg-blue-700"
        >
          Select File
        </label>
      </div>

      {file && (
        <div className="mt-6 flex items-center justify-between bg-gray-50 p-4 rounded-lg">
          <div>
            <p className="font-medium text-gray-900">{file.name}</p>
            <p className="text-sm text-gray-500">
              {(file.size / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
          <button
            onClick={handleUpload}
            disabled={uploading}
            className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {uploading ? "Uploading..." : "Upload & Process"}
          </button>
        </div>
      )}
    </div>
  );
}
