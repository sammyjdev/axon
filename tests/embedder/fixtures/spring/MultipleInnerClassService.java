package com.example.demo.service;

import org.springframework.stereotype.Service;

@Service
public class MultipleInnerClassService {

    static class RequestValidator {
        boolean validate(Object request) {
            return request != null;
        }

        String getErrorMessage(Object request) {
            return "Invalid request: " + request;
        }
    }

    static class ResponseBuilder {
        private int status;
        private Object body;

        ResponseBuilder status(int status) {
            this.status = status;
            return this;
        }

        ResponseBuilder body(Object body) {
            this.body = body;
            return this;
        }

        Response build() {
            return new Response(status, body);
        }
    }

    record Response(int status, Object body) {}

    private final RequestValidator validator = new RequestValidator();

    public Response process(Object request) {
        if (!validator.validate(request)) {
            return new ResponseBuilder().status(400).body(validator.getErrorMessage(request)).build();
        }
        return new ResponseBuilder().status(200).body(request).build();
    }

    public String describe(Object request) {
        return validator.validate(request) ? "valid" : "invalid";
    }
}
