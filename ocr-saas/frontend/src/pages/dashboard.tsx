import { FileText, Shield, Upload } from "lucide-react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { documentsApi } from "../services/api";

export function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ["stats"],
    queryFn: async () => {
      const [all, review] = await Promise.all([
        documentsApi.list(0, 1),
        documentsApi.list(0, 1, "review"),
      ]);
      return {
        totalDocuments: all.total,
        pendingReview: review.total,
      };
    },
  });

  const stats = [
    {
      label: "Total Documents",
      value: data?.totalDocuments ?? 0,
      icon: FileText,
      color: "bg-blue-500",
    },
    {
      label: "Pending Review",
      value: data?.pendingReview ?? 0,
      icon: Shield,
      color: "bg-yellow-500",
    },
    {
      label: "Processed Today",
      value: 0,
      icon: Upload,
      color: "bg-green-500",
    },
  ];

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="bg-white rounded-lg border border-gray-200 p-6"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">{stat.label}</p>
                {isLoading ? (
                  <div className="h-8 w-16 bg-gray-200 animate-pulse rounded mt-1" />
                ) : (
                  <p className="text-3xl font-bold text-gray-900 mt-1">
                    {stat.value}
                  </p>
                )}
              </div>
              <div className={`${stat.color} p-3 rounded-lg`}>
                <stat.icon className="w-6 h-6 text-white" />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">
          Quick Actions
        </h2>
        <div className="flex gap-4">
          <Link
            to="/upload"
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            Upload Document
          </Link>
          <Link
            to="/review"
            className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
          >
            Review Queue
          </Link>
        </div>
      </div>
    </div>
  );
}
