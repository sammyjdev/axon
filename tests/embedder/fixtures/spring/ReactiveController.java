package com.example.controller;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import java.time.Duration;
@RestController
@RequestMapping("/api/reactive")
public class ReactiveController {
    @GetMapping("/{id}")
    public Mono<String> getById(@PathVariable Long id) {
        return Mono.just("item-" + id).delayElement(Duration.ofMillis(10));
    }
    @GetMapping
    public Flux<String> getAll() {
        return Flux.range(1, 10).map(i -> "item-" + i).delayElements(Duration.ofMillis(5));
    }
    @PostMapping
    public Mono<String> create(@RequestBody String body) {
        return Mono.just(body).map(String::toUpperCase);
    }
    @DeleteMapping("/{id}")
    public Mono<Void> delete(@PathVariable Long id) {
        return Mono.empty();
    }
    @GetMapping("/stream")
    public Flux<Long> stream() {
        return Flux.interval(Duration.ofSeconds(1)).take(60);
    }
}
