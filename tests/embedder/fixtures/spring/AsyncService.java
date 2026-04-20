package com.example.service;
import org.springframework.scheduling.annotation.Async;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.stereotype.Service;
import java.util.concurrent.CompletableFuture;
@Service
@EnableAsync
public class AsyncService {
    @Async
    public CompletableFuture<String> processAsync(String input) {
        return CompletableFuture.supplyAsync(() -> {
            try { Thread.sleep(100); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            return input.toUpperCase();
        });
    }
    @Async("customExecutor")
    public CompletableFuture<Integer> computeAsync(int value) {
        return CompletableFuture.supplyAsync(() -> value * 2);
    }
    public CompletableFuture<String> combineResults(String a, String b) {
        return processAsync(a).thenCombine(processAsync(b), (ra, rb) -> ra + "-" + rb);
    }
    @Async
    public void fireAndForget(String message) {
        System.out.println("Processing: " + message);
    }
}
