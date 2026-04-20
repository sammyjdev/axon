package com.example.service;
import org.springframework.stereotype.Service;
import java.util.concurrent.CompletableFuture;
@Service
public class CircuitBreakerService {
    private static final String BACKEND = "backendService";
    public String callBackend(String request) {
        if (request.equals("fail")) throw new RuntimeException("Backend failure");
        return "response: " + request;
    }
    public CompletableFuture<String> callAsync(String request) {
        return CompletableFuture.supplyAsync(() -> "async: " + request);
    }
    public String callWithRetry(String request) {
        throw new RuntimeException("Always fails");
    }
    public String fallback(String request, Throwable t) {
        return "fallback for: " + request + " error: " + t.getMessage();
    }
    public CompletableFuture<String> asyncFallback(String request, Throwable t) {
        return CompletableFuture.completedFuture("async fallback: " + request);
    }
    public boolean isCircuitOpen() { return false; }
}
