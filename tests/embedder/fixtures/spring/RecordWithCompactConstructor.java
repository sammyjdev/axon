package com.example.demo.model;

import java.time.Instant;

public record RecordWithCompactConstructor(
        Long id,
        String title,
        String content,
        String authorEmail,
        Instant publishedAt,
        int wordCount
) {
    public RecordWithCompactConstructor {
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("title must not be blank");
        }
        if (authorEmail == null || !authorEmail.contains("@")) {
            throw new IllegalArgumentException("invalid author email");
        }
        if (wordCount < 0) {
            throw new IllegalArgumentException("wordCount must be >= 0");
        }
        title = title.trim();
        authorEmail = authorEmail.toLowerCase();
        if (publishedAt == null) {
            publishedAt = Instant.now();
        }
    }

    public boolean isPublished() {
        return publishedAt.isBefore(Instant.now());
    }

    public String summary() {
        return title + " by " + authorEmail + " (" + wordCount + " words)";
    }
}
