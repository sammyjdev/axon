package com.example.service;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
@Service
public class EventDrivenService {
    private final ApplicationEventPublisher publisher;
    public EventDrivenService(ApplicationEventPublisher publisher) { this.publisher = publisher; }
    public void triggerEvent(String data) { publisher.publishEvent(new DataEvent(this, data)); }
    @EventListener
    public void handleDataEvent(DataEvent event) {
        System.out.println("Handling: " + event.getData());
    }
    @EventListener
    @Async
    public void handleAsync(DataEvent event) {
        System.out.println("Async handling: " + event.getData());
    }
    @EventListener
    public void handleCritical(DataEvent event) {
        if (event.getData().startsWith("critical")) {
            System.out.println("CRITICAL: " + event.getData());
        }
    }
    public static class DataEvent extends org.springframework.context.ApplicationEvent {
        private final String data;
        public DataEvent(Object source, String data) { super(source); this.data = data; }
        public String getData() { return data; }
    }
}
