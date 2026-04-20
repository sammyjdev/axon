package com.example.service;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class ScheduledTaskService {
    private final java.util.List<String> reports;

    public ScheduledTaskService(java.util.List<String> reports) {
        this.reports = reports;
    }

    @Scheduled(cron = "0 0 8 * * MON-FRI")
    public void sendDailyReport() {
        System.out.println("Sending daily report with " + reports.size() + " items");
    }

    @Scheduled(fixedDelay = 3600000)
    public void cleanupExpiredSessions() {
        System.out.println("Cleaning expired sessions");
    }

    @Scheduled(fixedRate = 300000, initialDelay = 60000)
    public void syncExternalData() {
        System.out.println("Syncing external data");
    }
}
