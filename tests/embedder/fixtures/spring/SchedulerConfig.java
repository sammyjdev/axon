package com.example.config;
import org.springframework.context.annotation.*;
import org.springframework.scheduling.annotation.*;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;
import org.springframework.stereotype.Component;
@Configuration
@EnableScheduling
public class SchedulerConfig {
    @Bean
    public ThreadPoolTaskScheduler taskScheduler() {
        ThreadPoolTaskScheduler scheduler = new ThreadPoolTaskScheduler();
        scheduler.setPoolSize(5);
        scheduler.setThreadNamePrefix("scheduled-");
        return scheduler;
    }
    @Component
    public static class ScheduledTasks {
        @Scheduled(fixedRate = 5000)
        public void runEvery5Seconds() { System.out.println("Tick at: " + System.currentTimeMillis()); }
        @Scheduled(cron = "0 0 * * * *")
        public void runHourly() { System.out.println("Hourly task"); }
        @Scheduled(fixedDelay = 10000, initialDelay = 1000)
        public void runWithDelay() { System.out.println("Delayed task"); }
    }
}
