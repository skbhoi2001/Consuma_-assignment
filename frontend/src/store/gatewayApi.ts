// RTK Query endpoints for the gateway API. Polling replaces refetchInterval; tags replace
// manual invalidation (a limit change refreshes the model list and that model's history).

import { createApi, type BaseQueryFn } from "@reduxjs/toolkit/query/react";
import {
  api,
  type BatchSummary,
  type CallbackAttempt,
  type LimitChange,
  type ModelInfo,
  type ResultsPage,
  type RunDetail,
  type RunListItem,
} from "../api";

export type ApiError = { message: string };

// Endpoints run the existing typed `api` calls; a thrown Error becomes { message } so
// <ErrorNote> keeps showing "status: detail".
const callApi: BaseQueryFn<() => Promise<unknown>, unknown, ApiError> = async (call) => {
  try {
    return { data: await call() };
  } catch (err) {
    return { error: { message: err instanceof Error ? err.message : String(err) } };
  }
};

export const gatewayApi = createApi({
  reducerPath: "gatewayApi",
  baseQuery: callApi,
  tagTypes: ["Models", "History", "Batches", "Runs"],
  endpoints: (build) => ({
    getModels: build.query<ModelInfo[], void>({
      query: () => () => api.models(),
      providesTags: ["Models"],
    }),
    getLimitHistory: build.query<LimitChange[], string>({
      query: (model) => () => api.limitHistory(model),
      providesTags: (_r, _e, model) => [{ type: "History", id: model }],
    }),
    updateLimits: build.mutation<
      ModelInfo & { simulator_synced: boolean | null },
      { model: string; rpm?: number; tpm?: number }
    >({
      query: ({ model, ...body }) => () => api.updateLimits(model, body),
      invalidatesTags: (_r, _e, { model }) => ["Models", { type: "History", id: model }],
    }),

    getBatches: build.query<BatchSummary[], void>({
      query: () => () => api.batches(),
      providesTags: ["Batches"],
    }),
    getBatch: build.query<BatchSummary, string>({
      query: (id) => () => api.batch(id),
    }),
    getBatchCallbacks: build.query<CallbackAttempt[], string>({
      query: (id) => () => api.batchCallbacks(id),
    }),
    getBatchResults: build.query<ResultsPage, { id: string; cursor?: string }>({
      query: ({ id, cursor }) => () => api.batchResults(id, cursor),
    }),
    submitBatch: build.mutation<{ batch_id: string }, unknown>({
      query: (body) => () => api.submitBatch(body),
      invalidatesTags: ["Batches"],
    }),

    getRuns: build.query<RunListItem[], void>({
      query: () => () => api.runs(),
      providesTags: ["Runs"],
    }),
    getRun: build.query<RunDetail, string>({
      query: (id) => () => api.run(id),
    }),
    startRun: build.mutation<{ run_id: string }, unknown>({
      query: (body) => () => api.startRun(body),
      invalidatesTags: ["Runs"],
    }),
    stopRun: build.mutation<unknown, string>({
      query: (id) => () => api.stopRun(id),
      invalidatesTags: ["Runs"],
    }),
  }),
});

export const {
  useGetModelsQuery,
  useGetLimitHistoryQuery,
  useUpdateLimitsMutation,
  useGetBatchesQuery,
  useGetBatchQuery,
  useGetBatchCallbacksQuery,
  useGetBatchResultsQuery,
  useSubmitBatchMutation,
  useGetRunsQuery,
  useGetRunQuery,
  useStartRunMutation,
  useStopRunMutation,
} = gatewayApi;
