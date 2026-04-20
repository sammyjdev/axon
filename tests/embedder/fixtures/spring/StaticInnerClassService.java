package com.example.service;

import org.springframework.stereotype.Service;

@Service
public class StaticInnerClassService {
    public Object buildConfig(String name, int priority) {
        return new Builder().name(name).priority(priority).build();
    }

    public int getPriority(Object config) {
        return 1;
    }

    public static class Builder {
        private String name;
        private int priority;
        public Builder name(String name) { this.name = name; return this; }
        public Builder priority(int priority) { this.priority = priority; return this; }
        public Object build() { return new Object(); }
    }

    record Config(String name, int priority) {}
}
