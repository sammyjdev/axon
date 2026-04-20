package com.example.service;

import org.springframework.stereotype.Service;

@Service
public class NonStaticInnerClassService {
    private final String prefix;

    public NonStaticInnerClassService(String prefix) {
        this.prefix = prefix;
    }

    public String formatValue(Object value) {
        return new Formatter().format(value);
    }

    class Formatter {
        public String format(Object obj) {
            return prefix + ":" + obj.toString();
        }

        public String formatList(java.util.List<?> items) {
            return items.stream().map(Object::toString)
                .map(s -> prefix + ":" + s)
                .collect(java.util.stream.Collectors.joining(", "));
        }
    }
}
