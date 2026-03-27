import { useCallback, useEffect, useRef, useState } from "react";
import type { BoundingBox, TextBlock } from "../services/api";

interface DocumentViewerProps {
  imageUrl?: string;
  textBlocks?: TextBlock[];
  /** Direct bbox from bbox_evidence — used for highlight instead of text matching */
  selectedBbox?: BoundingBox;
  onFieldSelect?: (fieldKey: string, bbox?: BoundingBox) => void;
  zoom?: number;
  panOffset?: { x: number; y: number };
  onZoomChange?: (zoom: number) => void;
  onPanChange?: (offset: { x: number; y: number }) => void;
}

export function DocumentViewer({
  imageUrl,
  textBlocks = [],
  selectedBbox,
  onFieldSelect,
  zoom = 1,
  panOffset = { x: 0, y: 0 },
  onZoomChange,
  onPanChange,
}: DocumentViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });

  // Mouse wheel zoom — must be non-passive to call preventDefault
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const onWheel = (e: WheelEvent) => {
      if (!onZoomChange) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      const newZoom = Math.max(0.25, Math.min(4, zoom + delta));
      onZoomChange(newZoom);
    };
    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, [zoom, onZoomChange]);

  // Pan handling
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button === 1 || (e.button === 0 && e.altKey)) {
        setIsPanning(true);
        setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
      }
    },
    [panOffset]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (isPanning && onPanChange) {
        onPanChange({
          x: e.clientX - panStart.x,
          y: e.clientY - panStart.y,
        });
      }
    },
    [isPanning, panStart, onPanChange]
  );

  const handleMouseUp = useCallback(() => {
    setIsPanning(false);
  }, []);

  // Match text block by exact bbox coordinates from bbox_evidence
  const isBlockHighlighted = (block: TextBlock): boolean => {
    if (!selectedBbox || !block.bbox) return false;
    return (
      block.bbox.x1 === selectedBbox.x1 &&
      block.bbox.y1 === selectedBbox.y1 &&
      block.bbox.x2 === selectedBbox.x2 &&
      block.bbox.y2 === selectedBbox.y2
    );
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden bg-gray-100 rounded-lg"
      style={{ cursor: isPanning ? "grabbing" : "default" }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Zoom controls */}
      <div className="absolute top-4 right-4 z-10 flex flex-col gap-1.5">
        <button
          onClick={() => onZoomChange?.(Math.min(4, zoom + 0.25))}
          className="p-2.5 bg-blue-600 text-white rounded-lg shadow-lg hover:bg-blue-700 active:bg-blue-800 transition-colors"
          title="Zoom in"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
        </button>
        <div className="p-2 bg-gray-800 text-white rounded-lg shadow-lg text-xs font-semibold text-center min-w-[48px]">
          {Math.round(zoom * 100)}%
        </div>
        <button
          onClick={() => onZoomChange?.(Math.max(0.25, zoom - 0.25))}
          className="p-2.5 bg-blue-600 text-white rounded-lg shadow-lg hover:bg-blue-700 active:bg-blue-800 transition-colors"
          title="Zoom out"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M20 12H4" />
          </svg>
        </button>
        <button
          onClick={() => {
            onZoomChange?.(1);
            onPanChange?.({ x: 0, y: 0 });
          }}
          className="p-2.5 bg-gray-600 text-white rounded-lg shadow-lg hover:bg-gray-700 active:bg-gray-800 transition-colors"
          title="Reset view"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"
            />
          </svg>
        </button>
      </div>

      {/* Image container */}
      <div
        className="absolute inset-0 flex items-center justify-center"
        style={{
          transform: `translate(${panOffset.x}px, ${panOffset.y}px)`,
        }}
      >
        <div
          className="relative"
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: "center center",
          }}
        >
          {imageUrl ? (
            <img
              src={imageUrl}
              alt="Document"
              className="select-none shadow-lg"
              style={{ maxWidth: "800px", width: "100%", height: "auto" }}
              draggable={false}
            />
          ) : (
            <div className="w-[800px] h-[1100px] bg-white shadow-lg flex items-center justify-center text-gray-400">
              <span>No document image</span>
            </div>
          )}

          {/* SVG overlay for bounding boxes */}
          {imageUrl && (
            <svg
              className="absolute inset-0 w-full h-full pointer-events-none"
              style={{ overflow: "visible" }}
            >
              {/* All blocks — highlighted by direct bbox match */}
              {textBlocks.map((block, idx) => {
                if (!block.bbox) return null;
                const highlighted = isBlockHighlighted(block);
                return (
                  <rect
                    key={`block-${idx}`}
                    x={block.bbox.x1}
                    y={block.bbox.y1}
                    width={block.bbox.x2 - block.bbox.x1}
                    height={block.bbox.y2 - block.bbox.y1}
                    fill={highlighted ? "rgba(59, 130, 246, 0.25)" : "rgba(107, 114, 128, 0.08)"}
                    stroke={highlighted ? "#3b82f6" : "#9ca3af"}
                    strokeWidth={highlighted ? "2" : "1"}
                    strokeDasharray={highlighted ? "none" : "4 2"}
                    rx="2"
                    className="pointer-events-auto cursor-pointer transition-opacity hover:opacity-80"
                    onClick={() => {
                      if (block.text && onFieldSelect) {
                        onFieldSelect(block.text, block.bbox);
                      }
                    }}
                  />
                );
              })}
            </svg>
          )}
        </div>
      </div>

      {/* Pan hint */}
      <div className="absolute bottom-4 left-4 z-10 px-3 py-1.5 bg-black/50 text-white text-xs rounded-lg">
        Alt + Drag to pan • Scroll to zoom
      </div>
    </div>
  );
}
