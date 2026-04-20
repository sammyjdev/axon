package com.example.service;
import org.springframework.stereotype.Service;
@Service
public class RetryableService {
    private int callCount = 0;
    public String callExternalService(String input) {
        callCount++;
        if (callCount < 3) throw new RuntimeException("Temporary failure");
        return "success: " + input;
    }
    public String recover(RuntimeException e, String input) {
        return "fallback: " + input;
    }
    public void retryableVoid() {
        System.out.println("Trying...");
        throw new RuntimeException("Still failing");
    }
    public void recoverVoid(RuntimeException e) {
        System.out.println("Giving up after retries");
    }
    public int getCallCount() { return callCount; }
    public void resetCount() { callCount = 0; }
}
