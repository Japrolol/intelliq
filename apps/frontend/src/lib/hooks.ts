import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { request } from "./api";

export function useApiResource<T>(path: string | null, refreshInterval = 0) {
  const result = useQuery<T>({
    queryKey: ["api", path],
    queryFn: () => request<T>(path as string),
    enabled: Boolean(path),
    refetchInterval: refreshInterval || false,
    refetchIntervalInBackground: false,
    staleTime: refreshInterval > 0 ? 30_000 : 10_000,
  });

  return {
    data: result.data,
    error: result.error,
    isLoading: result.isLoading,
    isFetching: result.isFetching,
    mutate: async () => {
      const refreshed = await result.refetch();
      return refreshed.data;
    },
  };
}

export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(
    typeof navigator === "undefined" ? true : navigator.onLine,
  );

  useEffect(() => {
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  return online;
}
