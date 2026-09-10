#include "l2mm/orderbook.hpp"

#include <openssl/evp.h>

#include <chrono>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>

#ifndef L2MM_BUILD_TYPE
#define L2MM_BUILD_TYPE "unspecified"
#endif

namespace {

struct Operation {
    bool snapshot;
    std::uint64_t update_id;
    l2mm::InputLevels bids;
    l2mm::InputLevels asks;
};

std::uint64_t unsigned_integer(const std::string& text) {
    if (text.empty()) throw std::invalid_argument("empty integer");
    std::uint64_t result = 0;
    for (const char ch : text) {
        if (ch < '0' || ch > '9') throw std::invalid_argument("expected unsigned integer");
        const auto digit = static_cast<std::uint64_t>(ch - '0');
        if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10) {
            throw std::overflow_error("integer exceeds uint64 range");
        }
        result = result * 10 + digit;
    }
    return result;
}

std::string token(std::istream& input) {
    std::string result;
    if (!(input >> result)) throw std::invalid_argument("truncated operation input");
    return result;
}

bool read_operation(std::istream& input, Operation& operation) {
    std::string kind;
    if (!(input >> kind)) {
        if (input.eof()) return false;
        throw std::runtime_error("could not read operation input");
    }
    if (kind != "snapshot" && kind != "diff") {
        throw std::invalid_argument("unknown operation: " + kind);
    }
    operation.snapshot = kind == "snapshot";
    operation.update_id = unsigned_integer(token(input));
    const auto bids = unsigned_integer(token(input));
    const auto asks = unsigned_integer(token(input));
    constexpr std::uint64_t max_levels_per_side = 10000000;
    if (bids > max_levels_per_side || asks > max_levels_per_side) {
        throw std::invalid_argument("operation exceeds ten million levels per side");
    }
    const auto read_levels = [&input](auto count, auto& side) {
        side.clear();
        side.reserve(static_cast<std::size_t>(count));
        for (std::uint64_t i = 0; i < count; ++i) {
            auto price = token(input);
            auto quantity = token(input);
            side.emplace_back(std::move(price), std::move(quantity));
        }
    };
    read_levels(bids, operation.bids);
    read_levels(asks, operation.asks);
    return true;
}

void apply(l2mm::Orderbook& book, const Operation& operation) {
    if (operation.snapshot) book.apply_snapshot(operation.bids, operation.asks, operation.update_id);
    else book.apply_diff(operation.bids, operation.asks, operation.update_id);
}

std::string json_string(const std::string& value) {
    std::string result = "\"";
    for (const char ch : value) {
        if (ch == '"' || ch == '\\') result += '\\';
        if (ch == '\n') result += "\\n";
        else if (ch == '\r') result += "\\r";
        else if (ch == '\t') result += "\\t";
        else result += ch;
    }
    return result + '"';
}

std::string provenance() {
    return "\"compiler\":" + json_string(__VERSION__)
        + ",\"cpp_standard\":" + std::to_string(__cplusplus)
        + ",\"build_type\":" + json_string(L2MM_BUILD_TYPE);
}

// Incremental EVP hash keeps transcript mode bounded in the number of events.
class Transcript {
public:
    Transcript() : ctx_(EVP_MD_CTX_new(), EVP_MD_CTX_free) {
        if (!ctx_ || EVP_DigestInit_ex(ctx_.get(), EVP_sha256(), nullptr) != 1) {
            throw std::runtime_error("OpenSSL transcript initialization failed");
        }
    }
    void add(const std::string& hash) {
        if (EVP_DigestUpdate(ctx_.get(), hash.data(), hash.size()) != 1) {
            throw std::runtime_error("OpenSSL transcript update failed");
        }
    }
    std::string finish() {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int length = 0;
        if (EVP_DigestFinal_ex(ctx_.get(), digest, &length) != 1 || length != 32) {
            throw std::runtime_error("OpenSSL transcript finalization failed");
        }
        constexpr char hex[] = "0123456789abcdef";
        std::string result;
        for (unsigned int i = 0; i < length; ++i) {
            result += hex[digest[i] >> 4];
            result += hex[digest[i] & 15];
        }
        return result;
    }
private:
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx_;
};

void usage() {
    std::cout << "Usage: l2mm_orderbook trace|transcript|benchmark --tick-size DECIMAL --qty-step DECIMAL\n"
                 "       [benchmark only: --repeat N --warmup N]\n"
                 "       l2mm_orderbook --version\n"
                 "Input: snapshot|diff UPDATE_ID BID_COUNT ASK_COUNT, then bid rows and ask rows\n"
                 "       containing PRICE QUANTITY. Decimal tokens require exactly eight fractional digits.\n";
}

int run(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::cout << "{\"version\":\"0.1.0\"," << provenance() << "}\n";
        return 0;
    }
    if (argc == 1 || (argc == 2 && std::string(argv[1]) == "--help")) {
        usage();
        return argc == 1 ? 2 : 0;
    }
    const std::string mode = argv[1];
    if (mode != "trace" && mode != "transcript" && mode != "benchmark") {
        throw std::invalid_argument("mode must be trace, transcript, or benchmark");
    }
    std::string tick, step;
    std::uint64_t repeat = 5, warmup = 1;
    bool seen_repeat = false, seen_warmup = false;
    for (int i = 2; i < argc; ++i) {
        const std::string name = argv[i];
        if (++i >= argc) throw std::invalid_argument("missing value for " + name);
        const std::string value = argv[i];
        if (name == "--tick-size" && tick.empty()) tick = value;
        else if (name == "--qty-step" && step.empty()) step = value;
        else if (name == "--repeat" && mode == "benchmark" && !seen_repeat) {
            repeat = unsigned_integer(value);
            seen_repeat = true;
        } else if (name == "--warmup" && mode == "benchmark" && !seen_warmup) {
            warmup = unsigned_integer(value);
            seen_warmup = true;
        } else throw std::invalid_argument("unknown, duplicate, or inapplicable argument: " + name);
    }
    if (tick.empty() || step.empty()) throw std::invalid_argument("--tick-size and --qty-step are required");
    if (repeat == 0 || repeat > 100000 || warmup > 100000) {
        throw std::invalid_argument("repeat must be 1..100000 and warmup must be 0..100000");
    }
    const auto instrument = l2mm::Instrument::from_strings(tick, step);
    if (mode == "trace" || mode == "transcript") {
        l2mm::Orderbook book(instrument);
        Transcript transcript;
        std::uint64_t states = 1;
        if (mode == "trace") std::cout << book.trace_json() << '\n';
        else transcript.add(book.state_hash());
        Operation operation;
        while (read_operation(std::cin, operation)) {
            apply(book, operation);
            ++states;
            if (mode == "trace") std::cout << book.trace_json() << '\n';
            else transcript.add(book.state_hash());
        }
        if (mode == "transcript") {
            std::cout << "{\"states\":" << states
                      << ",\"rolling_digest\":\"" << transcript.finish()
                      << "\",\"final_state_hash\":\"" << book.state_hash() << "\"}\n";
        }
        return 0;
    }

    std::vector<Operation> operations;
    Operation operation;
    while (read_operation(std::cin, operation)) operations.push_back(std::move(operation));
    for (std::uint64_t i = 0; i < warmup; ++i) {
        l2mm::Orderbook book(instrument);
        for (const auto& op : operations) apply(book, op);
    }
    std::vector<std::int64_t> elapsed;
    std::string final_hash;
    for (std::uint64_t i = 0; i < repeat; ++i) {
        // Only construction and operations (including exact decimal parsing)
        // are timed. Input loading, hashing, reporting, and destruction are out.
        const auto start = std::chrono::steady_clock::now();
        l2mm::Orderbook book(instrument);
        for (const auto& op : operations) apply(book, op);
        const auto stop = std::chrono::steady_clock::now();
        elapsed.push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(stop - start).count());
        const auto hash = book.state_hash();
        if (!final_hash.empty() && hash != final_hash) throw std::runtime_error("benchmark repeats diverged");
        final_hash = hash;
    }
    std::cout << "{\"operations_per_repeat\":" << operations.size()
              << ",\"repeat\":" << repeat << ",\"warmup\":" << warmup
              << ",\"elapsed_ns\":[";
    for (std::size_t i = 0; i < elapsed.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << elapsed[i];
    }
    std::cout << "],\"final_state_hash\":\"" << final_hash << "\"," << provenance() << "}\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "l2mm_orderbook: " << error.what() << '\n';
        return 1;
    }
}
