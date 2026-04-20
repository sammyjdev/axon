package com.example.demo.client;

import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

@Component
public class WebClientWrapper {

    private final WebClient webClient;

    public WebClientWrapper(WebClient.Builder builder) {
        this.webClient = builder
                .baseUrl("https://api.example.com")
                .defaultHeader("Accept", "application/json")
                .filter((request, next) -> {
                    System.out.println("Request: " + request.method() + " " + request.url());
                    return next.exchange(request);
                })
                .build();
    }

    public Mono<String> get(String path) {
        return webClient.get()
                .uri(path)
                .retrieve()
                .bodyToMono(String.class)
                .onErrorResume(e -> Mono.error(new RuntimeException("GET failed: " + path, e)));
    }

    public Mono<String> post(String path, Object body) {
        return webClient.post()
                .uri(path)
                .bodyValue(body)
                .retrieve()
                .bodyToMono(String.class)
                .onErrorResume(e -> Mono.error(new RuntimeException("POST failed: " + path, e)));
    }
}
